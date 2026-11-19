"""#923: the Windows core is a cached runtime layer (Python + third-party packages + tlwu.exe) plus an app layer
(tinglongwu/, tlwu_app/, tinglongwu_setup/) that is repacked on every build."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "release" / "packaging"))
import build_windows as bw  # noqa: E402
import runtime_layer as rt  # noqa: E402

UNCHECKED_HASH_FLAGS = 0b01  # pyc header flags word: hash-based, check_source off


def _repo_copy(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    files = rt.key_files(REPO)
    names = {f.relative_to(REPO).as_posix() for f in files}
    # every file of the launcher and the stubs is in the key on every Python (3.12's glob("dir/**") gives none)
    assert {"release/packaging/launcher/tlwu_launcher.c", "release/packaging/launcher/tlwu.rc.in", "release/packaging/launcher/tlwu.manifest",
            "release/packaging/stubs/av/__init__.py", "release/packaging/runtime_layer.py", "requirements.txt"} <= names
    for f in files:
        dst = repo / f.relative_to(REPO)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    (repo / "tinglongwu" / "cli.py").write_text("x = 1\n")
    return repo


def test_runtime_key_follows_requirements_and_packaging_but_not_app_code(tmp_path):
    repo = _repo_copy(tmp_path)
    base = rt.key(True, repo)
    (repo / "tinglongwu" / "cli.py").write_text("x = 2\n")  # the acceptance case: a one-line app change
    assert rt.key(True, repo) == base
    assert rt.key(False, repo) != base and rt.key(True, repo, (3, 99)) != base  # variant, Python minor
    (repo / "tinglongwu" / "gui" / "assets" / "tinglongwu.ico").write_bytes(b"another icon")  # compiled into tlwu.exe
    assert rt.key(True, repo) != base
    shutil.copy2(rt.APP_ICON, repo / "tinglongwu" / "gui" / "assets" / "tinglongwu.ico")
    for rel in ("requirements.txt", "requirements-llama-cpu.txt", "release/packaging/runtime_layer.py",
                "release/packaging/build_windows.py", "release/packaging/launcher/tlwu_launcher.c", "release/packaging/stubs/av/__init__.py"):
        f = repo / rel
        old = f.read_bytes()
        f.write_bytes(old + b"\n# changed\n")
        assert rt.key(True, repo) != base, rel
        f.write_bytes(old.replace(b"\n", b"\r\n"))  # a CRLF checkout is the same layer
        assert rt.key(True, repo) == base, rel
        f.write_bytes(old)


def test_stale_reason_and_variants(tmp_path):
    layer = tmp_path / "runtime" / "tlwu"
    assert "no runtime layer" in rt.stale_reason(layer, True)
    layer.mkdir(parents=True)
    (layer / rt.EXE_NAME).write_bytes(b"MZ")
    man = rt.write_manifest(layer, True, "gcc")
    assert man["pyc_magic"] == importlib.util.MAGIC_NUMBER.hex() and man["key"] == rt.key(True)
    assert rt.stale_reason(layer, True) == "" and bw.runtime_stale(layer, True, False) == ""
    assert bw.runtime_stale(layer, False, True) == ""  # --allow-cpu-only accepts the CPU-only layer it built before
    assert "variant" in bw.runtime_stale(layer, False, False) or "changed" in bw.runtime_stale(layer, False, False)
    (layer / rt.MANIFEST_NAME).write_text(json.dumps({**man, "key": "0" * 64}), "utf-8")
    assert "changed" in bw.runtime_stale(layer, True, False)
    # a layer built by another Python minor is stale even though its own key is self-consistent: its packages,
    # stdlib zip and python3XY.dll do not fit the .pyc files this interpreter would write for the app layer
    other = {**man, "python_minor": [3, 99], "key": rt.key(True, pyver=(3, 99))}
    (layer / rt.MANIFEST_NAME).write_text(json.dumps(other), "utf-8")
    assert "changed" in bw.runtime_stale(layer, True, False)
    (layer / rt.MANIFEST_NAME).write_text(json.dumps({**man, "pyc_magic": "00000000"}), "utf-8")
    assert ".pyc format" in bw.runtime_stale(layer, True, False)
    (layer / rt.MANIFEST_NAME).write_text("not json", "utf-8")
    assert "no runtime layer" in bw.runtime_stale(layer, True, False)


def test_ensure_runtime_reuses_rebuilds_or_stops(tmp_path, monkeypatch):
    layer = tmp_path / "runtime" / "tlwu"
    layer.mkdir(parents=True)
    (layer / rt.EXE_NAME).write_bytes(b"MZ")
    man = rt.write_manifest(layer, True, "gcc")
    monkeypatch.setattr(bw, "build_runtime", lambda *a, **k: pytest.fail("an up-to-date layer is not rebuilt"))
    monkeypatch.setattr(rt, "make_pack_venv", lambda *a, **k: pytest.fail("no venv for an up-to-date layer"))
    assert bw.ensure_runtime(layer, True, False) == man
    assert bw.ensure_runtime(layer, True, False, reuse_only=True) == man

    (layer / rt.MANIFEST_NAME).unlink()
    with pytest.raises(SystemExit, match="--post-only"):
        bw.ensure_runtime(layer, True, False, reuse_only=True)
    # this interpreter is a packaging venv: built in-process
    monkeypatch.setattr(rt, "has_app_deps", lambda: True)
    monkeypatch.setattr(bw, "build_runtime", lambda lay, lite, cpu: {"built": (lay, lite, cpu)})
    assert bw.ensure_runtime(layer, True, False) == {"built": (layer, True, False)}
    # a plain Python (CI): --lite makes the packaging venv and builds the layer with it; the CUDA variant stops
    monkeypatch.setattr(rt, "has_app_deps", lambda: False)
    with pytest.raises(SystemExit, match="packaging venv"):
        bw.ensure_runtime(layer, False, False)
    venv_py = tmp_path / "venv" / "python"
    monkeypatch.setattr(rt, "make_pack_venv", lambda venv: venv_py)
    calls = []
    bw.ensure_runtime(layer, True, False, run=calls.append)
    assert calls == [[str(venv_py), str(REPO / "release" / "packaging" / "build_windows.py"), "--runtime-only",
                      "--rebuild-runtime", "--lite"]]


def test_link_tree_shares_runtime_files_but_copies_the_exe(tmp_path):
    src, dst = tmp_path / "runtime", tmp_path / "dist" / "tlwu"
    (src / "numpy" / "_core").mkdir(parents=True)
    (src / "numpy" / "_core" / "x.pyd").write_bytes(b"pyd")
    (src / "numpy" / "tlwu.exe").write_bytes(b"not the launcher")
    (src / "tlwu.exe").write_bytes(b"launcher")
    dst.mkdir(parents=True)
    (dst / "stale.txt").write_text("from the previous build")
    assert bw.link_tree(src, dst) == 3
    assert not (dst / "stale.txt").exists()
    assert os.path.samefile(src / "numpy" / "_core" / "x.pyd", dst / "numpy" / "_core" / "x.pyd")
    assert not os.path.samefile(src / "tlwu.exe", dst / "tlwu.exe")  # stamped with the version in place
    (dst / "tlwu.exe").write_bytes(b"stamped")
    assert (src / "tlwu.exe").read_bytes() == b"launcher"


def test_app_layer_is_source_plus_unchecked_hash_pyc(tmp_path):
    app = tmp_path / "tlwu"
    app.mkdir()
    (app / "tinglongwu").mkdir()
    (app / "tinglongwu" / "removed_module.py").write_text("old = 1\n")  # a file of the previous build
    assert bw.build_app_layer(app, rt.pyc_magic()) == list(bw.APP_PACKAGES)
    assert not (app / "tinglongwu" / "removed_module.py").exists()
    for pkg in bw.APP_PACKAGES:
        srcs = sorted(p.relative_to(REPO) for p in (REPO / pkg).rglob("*") if p.is_file()
                      and "__pycache__" not in p.parts)
        assert all((app / s).read_bytes() == (REPO / s).read_bytes() for s in srcs), pkg
    tag = sys.implementation.cache_tag
    pyc = app / "tlwu_app" / "__pycache__" / f"main.{tag}.pyc"
    head = pyc.read_bytes()[:8]
    assert head[:4] == importlib.util.MAGIC_NUMBER and int.from_bytes(head[4:8], "little") == UNCHECKED_HASH_FLAGS
    assert json.loads((app / bw.APP_MANIFEST).read_text("utf-8")) == {"version": bw.read_version(),
                                                                      "packages": list(bw.APP_PACKAGES)}
    # the packaged app starts from these files alone: tlwu_app.main is importable from the layer
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                        "import tlwu_app.main as m; print(m.__spec__.cached)", str(app)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and Path(r.stdout.strip()) == pyc, r.stderr

    # runtime layer of another Python: stop. Sources alone would lose to the previous install's unchecked-hash .pyc
    with pytest.raises(SystemExit, match="another .pyc format"):
        bw.build_app_layer(tmp_path / "other", "00000000")


def test_an_upgrade_never_runs_the_previous_versions_bytecode(tmp_path):
    """The wizard installs over the old folder without deleting. The new app layer brings its own .pyc for every
    module, so the old unchecked-hash .pyc (which Python would trust without looking at the .py) is replaced."""
    app = tmp_path / "tlwu"
    old_repo = tmp_path / "old"
    for pkg in bw.APP_PACKAGES:
        (old_repo / pkg).mkdir(parents=True)
        (old_repo / pkg / "__init__.py").write_text("")
    (old_repo / "tinglongwu" / "__init__.py").write_text('__version__ = "0.0.1"\n')
    (old_repo / "tlwu_app" / "main.py").write_text("WHO = 'old'\n")
    for rel in bw.WORKER_SOURCES:
        f = old_repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.exists() or f.write_text("")
    (old_repo / "release" / "packaging").mkdir(parents=True)
    (old_repo / "release" / "packaging" / bw.USAGE_TEXT).write_text("x", "utf-8")
    app.mkdir()
    bw.build_app_layer(app, rt.pyc_magic(), repo=old_repo)
    old_tree = tmp_path / "installed"
    shutil.copytree(app, old_tree)                       # the user's existing install
    new = tmp_path / "new_build"
    new.mkdir()
    bw.build_app_layer(new, rt.pyc_magic())              # the next release, built elsewhere
    shutil.copytree(new, old_tree, dirs_exist_ok=True)   # installed over it, nothing deleted
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                        "import tlwu_app.main as m; print(hasattr(m, 'WHO'), hasattr(m, 'is_bundled'))",
                        str(old_tree)], capture_output=True, text=True)
    assert r.stdout.split() == ["False", "True"], r.stderr


def test_stdlib_zip_is_importable_without_sources(tmp_path):
    lib = tmp_path / "Lib"
    for rel, text in (("mod923.py", "VALUE = 923\n"), ("pkg923/__init__.py", "from .sub import NAME\n"),
                      ("pkg923/sub.py", "NAME = 'sub'\n"), ("test/test_x.py", "x = 1\n"),
                      ("idlelib/idle.py", "x = 1\n"), ("site-packages/pip.py", "x = 1\n"), ("broken.py", "def (\n")):
        f = lib / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    out = tmp_path / rt.stdlib_zip_name()
    assert rt.make_stdlib_zip(lib, out) == 3
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["mod923.pyc", "pkg923/__init__.pyc", "pkg923/sub.pyc"]
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                        "import mod923, pkg923; print(mod923.VALUE, pkg923.NAME)", str(out)],
                       capture_output=True, text=True)
    assert r.stdout.split() == ["923", "sub"], r.stderr


def test_python_core_layout(tmp_path):
    """A python.org-style base Python -> python3XY.dll, DLLs/, tcl/, the stdlib zip and the ._pth that pins sys.path."""
    base, dst = tmp_path / "base", tmp_path / "layer"
    for rel in ("python312.dll", "python3.dll", "python.exe", "vcruntime140.dll", "DLLs/_tkinter.pyd",
                "DLLs/tcl86t.dll", "DLLs/py.ico", "DLLs/_msi.pyd", "DLLs/_testcapi.pyd", "DLLs/xxlimited_35.pyd",
                "tcl/nmake/x64/nmakehlp.exe", "tcl/tix8.4.3/Tix.tcl", "LICENSE.txt", "tcl/tcl8.6/init.tcl", "tcl/tk8.6/demos/widget",
                "tcl/dde1.4/tcldde14.dll", "tcl/tcl86t.lib", "Lib/os.py", "Lib/test/x.py"):
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n")
    dst.mkdir()
    rt.copy_python_core(dst, base)
    zname = rt.stdlib_zip_name()
    got = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert got == sorted(["python312.dll", "python3.dll", "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll",
                          "tcl/tcl8.6/init.tcl", zname, Path(zname).stem + "._pth",
                          "licenses/LICENSE-Python.txt"])
    # sys.path = stdlib zip, DLLs, the folder itself (packages sit at the top); no `import site`, no user site
    assert (dst / (Path(zname).stem + "._pth")).read_text("utf-8").split() == [zname, "DLLs", "."]
    (base / "LICENSE.txt").unlink()
    (tmp_path / "y").mkdir()
    with pytest.raises(SystemExit, match="Python license must ship"):
        rt.copy_python_core(tmp_path / "y", base)
    (base / "python312.dll").unlink()
    with pytest.raises(SystemExit, match="python3XY.dll"):
        rt.copy_python_core(tmp_path / "x", base)


def _boot_source() -> str:
    """The Python source the launcher passes to `-c` (the C string literals of BOOT, joined)."""
    c = (rt.LAUNCHER_DIR / "tlwu_launcher.c").read_text("utf-8")
    block = c[c.index("BOOT[] ="):]
    block = block[:block.index(";\n\n")]
    return "".join(part.replace("\\n", "\n") for part in re.findall(r'L"([^"]*)"', block))


def test_launcher_runs_the_app_entry_and_marks_the_process_bundled():
    c = (rt.LAUNCHER_DIR / "tlwu_launcher.c").read_text("utf-8")
    boot = _boot_source()
    compile(boot, "<boot>", "exec")
    assert "from tlwu_app.main import main" in boot and "sys.argv[0] = sys.executable" in boot
    assert 'L"TLWU_BUNDLED"' in c and '"Py_Main"' in c and 'L"-I"' in c  # is_bundled(); isolated from PYTHON* env
    from tinglongwu.gui import autoupdate
    from tlwu_app import main as app_main

    for mod in (app_main, autoupdate):  # the env name the launcher sets is the one the app reads
        assert 'os.environ.get("TLWU_BUNDLED") == "1"' in Path(mod.__file__).read_text("utf-8")
    rc = rt._rc_text(rt.APP_ICON, rt.LAUNCHER_DIR / "tlwu.manifest")
    assert "@" not in rc and "FILEVERSION 0,0,0,0" in rc and rt.APP_ICON.is_file()


def test_launcher_keeps_the_stream_settings_the_app_gives_its_children(tmp_path):
    """The ._pth makes Python ignore PYTHON* variables, but the GUI starts `tlwu.exe --run-setup` / `--selftest`
    children with PYTHONUNBUFFERED=1 + PYTHONIOENCODING=utf-8 and reads their output as UTF-8 (#804); the first
    release dry run of #923 died in `--selftest` printing Chinese to a cp1252 pipe."""
    c = (rt.LAUNCHER_DIR / "tlwu_launcher.c").read_text("utf-8")
    assert 'env_set(L"PYTHONUNBUFFERED", value)' in c and 'L"-u"' in c
    assert 'env_set(L"PYTHONUTF8", value) && wcscmp(value, L"1") == 0' in c and 'L"utf8"' in c
    for src in ("tinglongwu/gui/core.py", "tinglongwu/gui/autoupdate.py", "tinglongwu/vbcable.py",
                "tlwu_app/setup_gui.py"):  # the callers this exists for
        assert 'PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"' in (REPO / src).read_text("utf-8"), src
    # run the real BOOT in an isolated Python whose stdout would otherwise be ASCII-only
    pkg = tmp_path / "tlwu_app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text("import sys\ndef main():\n    print('\u807d\u9f8d', sys.argv[0] == sys.executable)\n"
                                 "    print(sys.stdout.encoding, sys.stdout.errors, sys.stderr.errors)\n    return 7\n")
    run = lambda env: subprocess.run(  # noqa: E731
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
                                     "sys.stdout.reconfigure(encoding='ascii'); exec(sys.argv[1])", _boot_source()],
        capture_output=True, env={**os.environ, **env})
    r = run({"PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 7 and r.stdout.decode("utf-8").split() == ["\u807d\u9f8d", "True", "utf-8", "strict",
                                                                      "backslashreplace"], r.stderr
    r = run({"PYTHONIOENCODING": "utf-8:replace"})
    assert r.stdout.decode("utf-8").split()[2:] == ["utf-8", "replace", "backslashreplace"]
    r = run({"PYTHONIOENCODING": "no-such-codec"})  # a bad value must not stop the app from starting
    assert r.returncode == 1 and b"UnicodeEncodeError" in r.stderr
    r = run({"PYTHONIOENCODING": ""})
    assert r.returncode == 1 and b"UnicodeEncodeError" in r.stderr  # unset: the stream's own encoding, as before


@pytest.mark.skipif(shutil.which("cc") is None or sys.platform == "win32", reason="needs a native C compiler")
def test_launcher_picks_the_newest_python_dll_not_the_first_in_the_folder(tmp_path):
    """An install over an older one can hold python312.dll and python313.dll; NTFS lists the old one first.
    The launcher's own name parser is compiled natively (wide-char calls mapped to their POSIX names) and run."""
    c = (rt.LAUNCHER_DIR / "tlwu_launcher.c").read_text("utf-8")
    fn = c[c.index("static int python_dll_minor("):c.index("/* Full path of the newest")]
    defs = "\n".join(ln for ln in c.splitlines() if ln.startswith("#define PYTHON_DLL_PREFIX"))
    names = ["python3.dll", "python312.dll", "python313.dll", "PYTHON39.DLL", "python3x.dll", "python312_d.dll",
             "python31234.dll", "python.dll", "python312.dll.bak"]
    (tmp_path / "t.c").write_text(
        "#include <stdio.h>\n#include <wchar.h>\n#define _wcsnicmp wcsncasecmp\n#define _wcsicmp wcscasecmp\n"
        + defs + "\n" + fn + "int main(void) {\n"
        + "".join(f'    printf("%d ", python_dll_minor(L"{n}"));\n' for n in names) + "    return 0;\n}\n")
    subprocess.check_call(["cc", "-Wall", "-Werror", "-o", str(tmp_path / "t"), str(tmp_path / "t.c")])
    got = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True).stdout.split()
    assert got == ["-1", "12", "13", "9", "-1", "-1", "1234", "-1", "-1"]
    assert "if (minor > best)" in c  # find_python_dll keeps the highest, whatever the directory order


@pytest.mark.skipif(rt._gcc() is None, reason="needs a MinGW-w64 gcc + windres on PATH")
def test_launcher_compiles_to_an_x64_exe_whose_version_the_app_layer_stamps(tmp_path):
    exe = tmp_path / "tlwu.exe"
    rt.build_launcher(exe, tmp_path / "work")
    data = exe.read_bytes()
    assert bw.pe_machine(data) == 0x8664 and bw.pe_file_version(data) == (0, 0, 0, 0)
    assert bw.set_exe_version(exe, "0.3.5.0") and bw.pe_file_version(exe.read_bytes()) == (0, 3, 5, 0)
    assert len(exe.read_bytes()) == len(data)
    with pytest.raises(SystemExit, match="0..65535"):  # a part that does not fit its 16-bit word
        bw.set_exe_version(exe, "0.3.70000.0")


def test_windows_build_runs_for_app_changes_and_skips_the_venv_on_a_cache_hit():
    import yaml

    text = (REPO / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    steps = wf["jobs"]["build"]["steps"]
    filt = next(str(s["run"]) for s in steps if s.get("id") == "changes")
    pattern = re.search(r"grep -Eq '([^']+)'", filt).group(1)
    for changed in ("tinglongwu/cli.py", "tinglongwu/gui/app.py", "tlwu_app/main.py", "tinglongwu_setup/cli.py",
                    "release/packaging/build_windows.py", "requirements.txt"):
        assert re.search(pattern, changed), changed
    for skipped in ("docs/install.md", "README.md", "tests/test_cli.py"):
        assert not re.search(pattern, skipped), skipped
    runs = "\n".join(str(s.get("run", "")) for s in steps)
    assert "pip install" not in runs and "nuitka" not in text.lower() and "PACK_PY" not in text
    pythons = [s for s in steps if "actions/setup-python@" in str(s.get("uses", ""))]
    hit = [s for s in pythons if "cache-hit == 'true'" in s["if"]]
    miss = [s for s in pythons if "cache-hit != 'true'" in s["if"]]
    assert len(hit) == len(miss) == 1 and "cache" not in hit[0]["with"] and miss[0]["with"]["cache"] == "pip"
    assert wf["jobs"]["build"]["timeout-minutes"] <= 30


def test_no_python_compiler_in_the_build():
    for f in ("release/packaging/build_windows.py", "release/packaging/runtime_layer.py"):
        code = (REPO / f).read_text("utf-8")
        assert '"-m", "nuitka"' not in code and "run_nuitka" not in code, f
    assert not (REPO / "release" / "packaging" / "tlwu_entry.py").exists()
    assert bw.APP_PACKAGES == rt.APP_PACKAGES


def test_licenses_folder_holds_the_texts_the_notice_names(tmp_path, monkeypatch):
    """THIRD_PARTY_NOTICES.txt points at LICENSE-libsndfile-LGPL-2.1.txt and LICENSE-Python.txt: both must ship."""
    site = tmp_path / "site"
    (site / "_soundfile_data").mkdir(parents=True)
    (site / "_soundfile_data" / "COPYING").write_text("LGPL text")
    (site / "soundfile.py").write_text("")  # a single module beside _soundfile_data/, as the wheel installs it
    spec = importlib.util.spec_from_file_location("soundfile", site / "soundfile.py")
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: spec if name == "soundfile" else real(name, *a))
    monkeypatch.setattr(bw, "REPO", tmp_path / "no-repo")  # no project .venv to fall back to
    app = tmp_path / "app"
    app.mkdir()
    bw.write_licenses(app, None)
    assert (app / "licenses" / "LICENSE-libsndfile-LGPL-2.1.txt").read_text() == "LGPL text"
    notice = (app / "licenses" / "THIRD_PARTY_NOTICES.txt").read_text("utf-8")
    for named in ("LICENSE-libsndfile-LGPL-2.1.txt", "LICENSE-Python.txt", "MinGW-w64"):
        assert named in notice
    assert "NVIDIA" not in notice  # CPU-only package


def test_no_top_level_wheel_in_the_core_folder_or_zip(tmp_path):
    """#948 D1: a stray *.whl at the top of site-packages is not copied, and make_zip leaves one out."""
    site = tmp_path / "site"
    (site / "pkg").mkdir(parents=True)
    (site / "pkg" / "__init__.py").write_text("")
    (site / "llama_cpp_python-1.0-win_amd64.whl").write_bytes(b"PK")
    dst = tmp_path / "core"
    dst.mkdir()
    assert rt.copy_site_packages(dst, site) == ["pkg"]
    assert not list(dst.glob("*.whl"))
    app = tmp_path / "tlwu"
    app.mkdir()
    (app / "tlwu.exe").write_bytes(b"x")
    (app / "stray.whl").write_bytes(b"PK")
    (app / "sub").mkdir()
    (app / "sub" / "keep.whl").write_bytes(b"PK")  # only the top level is checked
    out = tmp_path / "core.zip"
    bw.make_zip(app, out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "tlwu/tlwu.exe" in names and "tlwu/stray.whl" not in names


def test_mingw_copying_ships_under_licenses(tmp_path):
    """#948 D7: licenses/COPYING.MinGW-w64-runtime.txt comes from the gcc toolchain; MSVC builds ship none."""
    root = tmp_path / "mingw64"
    (root / "bin").mkdir(parents=True)
    gcc = root / "bin" / "gcc.exe"
    gcc.write_text("")
    (root / "share" / "licenses" / "mingw-w64-crt").mkdir(parents=True)
    (root / "share" / "licenses" / "mingw-w64-crt" / "COPYING").write_text("MinGW-w64 runtime licensing")
    (root / "share" / "licenses" / "zlib").mkdir()
    (root / "share" / "licenses" / "zlib" / "COPYING").write_text("zlib")
    dst = tmp_path / "layer"
    dst.mkdir()
    assert rt.copy_mingw_copying("msvc", dst, str(gcc)) is False and not (dst / "licenses").exists()
    assert rt.copy_mingw_copying("gcc.exe", dst, str(gcc)) is True
    assert (dst / "licenses" / rt.MINGW_COPYING_NAME).read_text() == "MinGW-w64 runtime licensing"
    bare = tmp_path / "bare" / "bin"
    bare.mkdir(parents=True)
    (bare / "gcc.exe").write_text("")
    assert rt.copy_mingw_copying("gcc.exe", tmp_path / "layer2", str(bare / "gcc.exe")) is False
    assert rt.MINGW_COPYING_NAME in bw.NOTICES_HEAD.replace("COPYING.MinGW-w64-runtime.txt", rt.MINGW_COPYING_NAME)
