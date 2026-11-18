r"""Runtime layer of the Windows core (#923): Python + every third-party package + the tlwu.exe launcher.

The core folder (dist/tlwu, what the zip and the setup wizard ship) is two layers:

  runtime layer  tlwu.exe (packaging/launcher), python3XY.dll, python3XY._pth, python3XY.zip (standard library),
                 DLLs/, tcl/, every third-party package of the packaging venv as .pyc, llama.cpp DLLs, VC++ runtime,
                 licenses/. Built by build() below. It depends only on the files in KEY_FILES (requirements*.txt and
                 the packaging code), so CI keeps it in a cache and rebuilds it only when key() changes.
  app layer      tinglongwu/, tlwu_app/, tinglongwu_setup/ as .py + .pyc. Repacked on every build by
                 build_windows.py (seconds).

No compiler runs over Python code (the old Nuitka standalone build compiled the app and every dependency: 13 min warm,
~40 min cold for a one-line change). tlwu.exe is ~100 lines of C that load python3XY.dll and run tlwu_app.main.
Packages sit at the top of the folder (not in Lib/site-packages), the layout the app already expects:
tinglongwu/device.py looks for llama_cpp/lib and cuda_libs/ next to the tinglongwu package.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER_DIR = REPO / "packaging" / "launcher"
STUBS_DIR = REPO / "packaging" / "stubs"  # av: PyAV's wheel bundles GPL FFmpeg codecs, so a stub ships instead
APP_ICON = REPO / "tinglongwu" / "gui" / "assets" / "tinglongwu.ico"  # same icon as the wizard and shortcuts (#127)
EXE_NAME = "tlwu.exe"
GUI_EXE_NAME = "tlwu-gui.exe"  # #1524: GUI-subsystem stub the shortcuts start; it runs tlwu.exe without a console window
MANIFEST_NAME = "runtime-layer.json"  # at the top of the layer: key, Python version, pyc magic
# The runtime layer is rebuilt when one of these changes (repo-relative; a folder = every file in it). The two
# workflows hash the same list for their cache key (tests/test_windows_layers_923.py keeps them equal).
KEY_GLOBS = ("requirements*.txt", "packaging/build_windows.py", "packaging/runtime_layer.py",
             "packaging/launcher/**", "packaging/stubs/**", "tinglongwu/gui/assets/tinglongwu.ico")
# Packages of the packaging venv that are build tools, not part of the app
NOT_SHIPPED = {"pip", "setuptools", "pkg_resources", "_distutils_hack", "wheel", "nuitka", "ordered_set",
               "zstandard", "av", "psutil",  # names as _dist_name() gives them
               # CMake install folders that the llama-cpp-python wheel drops into site-packages (llama.cpp tools,
               # headers, import libraries); the app loads llama_cpp/lib only
               "bin", "include", "lib", "share"}
APP_PACKAGES = ("tinglongwu", "tlwu_app", "tinglongwu_setup")  # the app layer; never copied from site-packages
# Standard-library folders the app cannot use (tests, IDLE, pip bootstrap, demos)
STDLIB_SKIP_DIRS = {"site-packages", "dist-packages", "__pycache__", "test", "tests", "idlelib", "ensurepip", "turtledemo",
                    "lib2to3", "__phello__"}
PRUNE_TESTS_IN = ("numpy", "scipy", "colorama")  # tests/ folders (~40 MB) that the app never imports
# not needed by tkinter: demos, C development files, and the dde / registry Tcl extensions (DLLs nobody loads)
TCL_SKIP = ("demos", "*.lib", "*.sh", "include", "pkgconfig", "dde*", "reg*", "nmake", "tix*")
# DLLs/ files left out (fnmatch, lower case): Windows Installer bindings (unused; msi.dll is not on the clean-machine
# DLL list) and CPython's own test helper modules
DLLS_SKIP = ("_msi.pyd", "_test*.pyd", "_ctypes_test.pyd", "xxlimited*.pyd")
PYTHON_LICENSE = "LICENSE.txt"  # in the base Python; shipped as licenses/LICENSE-Python.txt
PTH_LINES = ("{zip}", "DLLs", ".")  # sys.path of tlwu.exe, relative to the folder; no `import site`
REQ_NOT_PACKED = ("av", "psutil")   # requirements.txt lines left out of the packaging venv (GPL codecs / dev only)


def key_files(repo: Path = REPO) -> list[Path]:
    out: set[Path] = set()
    for g in KEY_GLOBS:
        # "dir/**" = every file under dir, as in the workflows' hashFiles(); Path.glob("dir/**") gives folders only
        # before Python 3.13, so it is walked by hand
        found = (repo / g[:-3]).rglob("*") if g.endswith("/**") else repo.glob(g)
        out.update(p for p in found if p.is_file() and "__pycache__" not in p.parts)
    return sorted(out)


def key(lite: bool, repo: Path = REPO, pyver: tuple[int, int] | None = None) -> str:
    """sha256 over KEY_FILES (path + content, line endings normalised), the Python minor version and the variant."""
    major, minor = pyver or sys.version_info[:2]
    h = hashlib.sha256(f"py{major}{minor};{'lite' if lite else 'cuda'}\0".encode())
    for f in key_files(repo):
        h.update(f.relative_to(repo).as_posix().encode() + b"\0" + f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def pyc_magic() -> str:
    return importlib.util.MAGIC_NUMBER.hex()


def read_manifest(layer: Path) -> dict:
    try:
        data = json.loads((layer / MANIFEST_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def stale_reason(layer: Path, lite: bool, repo: Path = REPO) -> str:
    """Why `layer` cannot be reused ('' = it is up to date)."""
    man = read_manifest(layer)
    if not man or not (layer / EXE_NAME).is_file():
        return f"no runtime layer in {layer}"
    if man.get("key") != key(lite, repo):  # the key holds this interpreter's Python minor version
        return "requirements*.txt, the packaging code or the Python version changed"
    if man.get("pyc_magic") != pyc_magic():
        return "the runtime layer was built by a Python with another .pyc format"
    return ""


# ---- Python itself -------------------------------------------------------------------------------------------------

def stdlib_zip_name() -> str:
    return f"python{sys.version_info[0]}{sys.version_info[1]}.zip"


def _compile_to(src: Path, cfile: Path) -> bool:
    """Unchecked hash-based .pyc: never compared with a source file, so it works without the .py and whatever the
    file times are after unzip / install. False = the file does not compile (left as it is)."""
    try:
        py_compile.compile(str(src), cfile=str(cfile), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    except (py_compile.PyCompileError, OSError, ValueError, RecursionError):
        return False
    return True


def make_stdlib_zip(lib: Path, out: Path) -> int:
    """Lib/ of the base Python as a .pyc-only zip (the layout of python.org's embeddable package). Returns the count."""
    n = 0
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        cfile = Path(tmp) / "m.pyc"
        for root, dirs, files in os.walk(lib):
            dirs[:] = sorted(d for d in dirs if d not in STDLIB_SKIP_DIRS)
            for name in sorted(files):
                src = Path(root) / name
                if src.suffix == ".py" and _compile_to(src, cfile):
                    z.write(cfile, src.relative_to(lib).with_suffix(".pyc").as_posix())
                    n += 1
    return n


def copy_python_core(dst: Path, base: Path | None = None) -> list[str]:
    """python3XY.dll, python3.dll, DLLs/ (extension modules and their DLLs), tcl/ (Tcl/Tk scripts for tkinter), the
    standard library zip and the ._pth file that pins sys.path, all from the base Python of this interpreter."""
    base = Path(base or sys.base_prefix)
    dlls = sorted(base.glob("python3*.dll"))
    if not any(f.name.lower() != "python3.dll" for f in dlls):
        raise SystemExit(f"runtime layer: no python3XY.dll in {base} (needs a python.org-style Windows Python)")
    for f in dlls:
        shutil.copy2(f, dst / f.name)
    (dst / "DLLs").mkdir(exist_ok=True)
    for f in sorted((base / "DLLs").glob("*")):
        if f.suffix.lower() in (".pyd", ".dll") and not any(fnmatch.fnmatch(f.name.lower(), p) for p in DLLS_SKIP):
            shutil.copy2(f, dst / "DLLs" / f.name)
    if not (base / "tcl").is_dir():
        raise SystemExit(f"runtime layer: {base}\\tcl is missing (Python was installed without tcl/tk; the GUI needs it)")
    shutil.copytree(base / "tcl", dst / "tcl", ignore=shutil.ignore_patterns(*TCL_SKIP), dirs_exist_ok=True)
    zname = stdlib_zip_name()
    count = make_stdlib_zip(base / "Lib", dst / zname)
    pth = dst / (Path(zname).stem + "._pth")
    pth.write_text("".join(line.format(zip=zname) + "\n" for line in PTH_LINES), "utf-8")
    lic = base / PYTHON_LICENSE  # the PSF license text: the folder redistributes CPython and its standard library
    if not lic.is_file():
        raise SystemExit(f"runtime layer: {lic} is missing; the Python license must ship with the package")
    (dst / "licenses").mkdir(exist_ok=True)
    shutil.copy2(lic, dst / "licenses" / "LICENSE-Python.txt")
    return [f.name for f in dlls] + [f"{zname} ({count} modules)", pth.name]


# ---- third-party packages ------------------------------------------------------------------------------------------

def _dist_name(entry: str) -> str:
    """'numpy-2.5.4.dist-info' -> 'numpy'; 'numpy' -> 'numpy'; 'six.py' -> 'six' (lower case, '-' -> '_')."""
    name = entry
    for suffix in (".dist-info", ".egg-info", ".py", ".libs"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            if suffix in (".dist-info", ".egg-info"):
                name = name.split("-")[0]
            break
    return name.lower().replace("-", "_")


def _shipped(entry: str) -> bool:
    if entry == "__pycache__" or entry.endswith((".pth", ".egg-link", ".whl")) or entry.startswith(("__editable__", "~")):
        return False
    return _dist_name(entry) not in NOT_SHIPPED and _dist_name(entry) not in APP_PACKAGES


def copy_site_packages(dst: Path, site: Path | None = None) -> list[str]:
    """Every package of the packaging venv's site-packages to the top of dst (build tools and the app left out)."""
    site = Path(site or sysconfig.get_paths()["purelib"])
    copied = []
    for entry in sorted(site.iterdir(), key=lambda p: p.name.lower()):
        if not _shipped(entry.name):
            continue
        if entry.is_dir():
            shutil.copytree(entry, dst / entry.name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyi", "*.whl"))
        else:
            shutil.copy2(entry, dst / entry.name)
        copied.append(entry.name)
    for pkg in PRUNE_TESTS_IN:
        for tests in sorted((dst / pkg).rglob("tests")) if (dst / pkg).is_dir() else ():
            if tests.is_dir():
                shutil.rmtree(tests)
    return copied


def copy_stubs(dst: Path, stubs: Path = STUBS_DIR) -> list[str]:
    names = []
    for entry in sorted(stubs.iterdir()):
        if entry.is_dir():
            shutil.copytree(entry, dst / entry.name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            names.append(entry.name)
    return names


def compile_sourceless(root: Path, skip_top: tuple[str, ...] = ("tcl", "DLLs", "licenses")) -> tuple[int, int]:
    """Replace every x.py under root by x.pyc beside it (third-party code ships without sources, as it did in the
    compiled build). A file that does not compile keeps its .py. Returns (compiled, kept)."""
    done = kept = 0
    for top in sorted(root.iterdir()):
        if top.name in skip_top:
            continue
        for src in sorted(top.rglob("*.py")) if top.is_dir() else ([top] if top.suffix == ".py" else []):
            if _compile_to(src, src.with_suffix(".pyc")):
                src.unlink()
                done += 1
            else:
                kept += 1
    return done, kept


# ---- tlwu.exe ------------------------------------------------------------------------------------------------------

def _rc_text(icon: Path, manifest: Path) -> str:
    text = (LAUNCHER_DIR / "tlwu.rc.in").read_text("utf-8")
    return text.replace("@ICON@", icon.as_posix()).replace("@MANIFEST@", manifest.as_posix())


def _gcc() -> tuple[str, str] | None:
    """(gcc, windres) of a MinGW-w64 x64 toolchain on PATH (the cross compiler names work on Linux / macOS)."""
    for gcc, windres in (("x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-windres"), ("gcc", "windres")):
        g, w = shutil.which(gcc), shutil.which(windres)
        if g and w and (gcc != "gcc" or sys.platform == "win32"):
            return g, w
    return None


def _vcvars() -> Path | None:
    """vcvars64.bat of the newest Visual Studio / Build Tools with the C++ x64 tools (found through vswhere)."""
    pf = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles")
    vswhere = Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe" if pf else None
    if not (vswhere and vswhere.is_file()):
        return None
    r = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                       capture_output=True, text=True)
    bat = Path(r.stdout.strip().splitlines()[0]) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat" if r.stdout.strip() else None
    return bat if bat and bat.is_file() else None


def _launcher_builds(out: Path, work: Path, rc: Path, src: Path, gui: bool = False) -> list[tuple[str, list[list[str]]]]:
    """(tool name, commands) for every C toolchain found on this machine, preferred first. gui=True builds the
    GUI-subsystem stub (#1524) instead of the console launcher."""
    builds = []
    gcc = _gcc()
    if gcc:
        res = work / "tlwu.res.o"
        builds.append((Path(gcc[0]).name, [
            [gcc[1], str(rc), "-O", "coff", "-o", str(res)],
            [gcc[0], "-O2", "-s", "-Wall", "-municode", *(["-mwindows"] if gui else []), "-o", str(out), str(src),
             str(res), "-luser32"]]))
    bat = _vcvars() if sys.platform == "win32" else None
    if bat:
        res = work / "tlwu.res"
        script = work / "build_launcher.cmd"  # a .cmd file: no quoting games with `cmd /c "a && b"`
        script.write_text("@echo off\r\n"
                          f'call "{bat}" >nul || exit /b 1\r\n'
                          f'rc /nologo /fo "{res}" "{rc}" || exit /b 1\r\n'
                          f'cl /nologo /O2 /MT /W3 /Fo"{work}\\\\" /Fe"{out}" "{src}" "{res}" '
                          f"/link /SUBSYSTEM:{'WINDOWS' if gui else 'CONSOLE'} /MANIFEST:NO user32.lib || exit /b 1\r\n", "utf-8")
        builds.append(("msvc", [["cmd", "/c", str(script)]]))
    return builds


MINGW_COPYING_NAME = "COPYING.MinGW-w64-runtime.txt"  # in licenses/, next to LICENSE-Python.txt
MINGW_LICENSE_DIRS = ("share/licenses", "share/doc")  # under the toolchain root (the folder above gcc's bin/)


def find_mingw_copying(gcc: str) -> Path | None:
    """The MinGW-w64 runtime's COPYING file of the toolchain that `gcc` belongs to (None = not found)."""
    root = Path(gcc).resolve().parent.parent
    for rel in MINGW_LICENSE_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("COPYING*")):
            if f.is_file() and any(k in str(f.parent).lower() for k in ("mingw", "crt")):
                return f
    return None


def copy_mingw_copying(tool: str, dst: Path, gcc: str | None = None) -> bool:
    """licenses/COPYING.MinGW-w64-runtime.txt when tlwu.exe was built by MinGW-w64 (it links the runtime); `tool` is
    build_launcher()'s return value. False = built with MSVC, or the toolchain has no COPYING file."""
    if tool == "msvc":
        return False
    gcc = gcc or (_gcc() or ("",))[0]
    src = find_mingw_copying(gcc) if gcc else None
    if src is None:
        return False
    (dst / "licenses").mkdir(exist_ok=True)
    shutil.copy2(src, dst / "licenses" / MINGW_COPYING_NAME)
    return True


def build_launcher(out: Path, work: Path, run=subprocess.run, gui: bool = False) -> str:
    """Compile packaging/launcher/tlwu_launcher.c (+ icon, version resource, manifest) to `out` with the first
    toolchain that works (MinGW-w64 gcc, then MSVC). Returns the tool's name. gui=True compiles
    tlwu_gui_stub.c as a WINDOWS-subsystem exe (tlwu-gui.exe, #1524)."""
    work.mkdir(parents=True, exist_ok=True)
    rc = work / "tlwu.rc"
    rc.write_text(_rc_text(APP_ICON, LAUNCHER_DIR / "tlwu.manifest"), "utf-8")
    errors = []
    src = LAUNCHER_DIR / ("tlwu_gui_stub.c" if gui else "tlwu_launcher.c")
    for tool, cmds in _launcher_builds(out, work, rc, src, gui=gui):
        out.unlink(missing_ok=True)
        for cmd in cmds:
            r = run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                errors.append(f"{tool}: {' '.join(cmd)} failed (exit {r.returncode}):\n{r.stdout}{r.stderr}")
                break
        else:
            if out.is_file():
                return tool
            errors.append(f"{tool}: no {out.name} written")
    raise SystemExit(f"runtime layer: {out.name} was not built. Needs MinGW-w64 (gcc + windres on PATH) or the Visual "
                     "Studio Build Tools (C++ x64).\n" + "\n".join(errors))


# ---- packaging venv ------------------------------------------------------------------------------------------------

def has_app_deps() -> bool:
    """This interpreter is a packaging venv (the app's packages are importable)."""
    return all(importlib.util.find_spec(m) is not None for m in ("faster_whisper", "llama_cpp", "sv_ttk"))


def pack_requirements(repo: Path = REPO) -> str:
    """requirements.txt without the REQ_NOT_PACKED pins."""
    lines = (repo / "requirements.txt").read_text("utf-8").splitlines()
    return "".join(ln + "\n" for ln in lines if ln.split("==")[0].strip().lower() not in REQ_NOT_PACKED)


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")


def make_pack_venv(venv: Path, repo: Path = REPO, run=subprocess.check_call) -> Path:
    """The CPU packaging venv: requirements.txt without av / psutil + the CPU llama-cpp-python wheel (binary only)."""
    if venv.exists():
        shutil.rmtree(venv)
    run([sys.executable, "-m", "venv", str(venv)])
    py = str(venv_python(venv))
    req = venv.parent / "req-pack.txt"
    req.write_text(pack_requirements(repo), "utf-8")
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-r", str(req)])
    run([py, "-m", "pip", "install", "-r", str(repo / "requirements-llama-cpu.txt"), "--only-binary=:all:"])
    return Path(py)


def write_manifest(layer: Path, lite: bool, launcher_tool: str) -> dict:
    man = {"key": key(lite), "lite": lite, "python": sys.version.split()[0],
           "python_minor": list(sys.version_info[:2]), "pyc_magic": pyc_magic(), "launcher": launcher_tool}
    (layer / MANIFEST_NAME).write_text(json.dumps(man, indent=1) + "\n", "utf-8")
    return man
