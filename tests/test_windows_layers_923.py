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
sys.path.insert(0, str(REPO / "packaging"))
import build_windows as bw  # noqa: E402
import runtime_layer as rt  # noqa: E402

UNCHECKED_HASH_FLAGS = 0b01  # pyc header flags word: hash-based, check_source off


def _repo_copy(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    files = rt.key_files(REPO)
    names = {f.relative_to(REPO).as_posix() for f in files}
    # every file of the launcher and the stubs is in the key on every Python (3.12's glob("dir/**") gives none)
    assert {"packaging/launcher/tlwu_launcher.c", "packaging/launcher/tlwu.rc.in", "packaging/launcher/tlwu.manifest",
            "packaging/stubs/av/__init__.py", "packaging/runtime_layer.py", "requirements.txt"} <= names
    for f in files:
        dst = repo / f.relative_to(REPO)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    (repo / "tinglongwu").mkdir()
    (repo / "tinglongwu" / "cli.py").write_text("x = 1\n")
    return repo


def test_runtime_key_follows_requirements_and_packaging_but_not_app_code(tmp_path):
    repo = _repo_copy(tmp_path)
    base = rt.key(True, repo)
    (repo / "tinglongwu" / "cli.py").write_text("x = 2\n")  # the acceptance case: a one-line app change
    assert rt.key(True, repo) == base
    assert rt.key(False, repo) != base and rt.key(True, repo, (3, 99)) != base  # variant, Python minor
    for rel in ("requirements.txt", "requirements-llama-cpu.txt", "packaging/runtime_layer.py",
                "packaging/build_windows.py", "packaging/launcher/tlwu_launcher.c", "packaging/stubs/av/__init__.py"):
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
    assert calls == [[str(venv_py), str(REPO / "packaging" / "build_windows.py"), "--runtime-only",
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

    other = tmp_path / "other"
    other.mkdir()
    bw.build_app_layer(other, "00000000")  # runtime layer of another Python: sources only, never a wrong .pyc
    assert (other / "tlwu_app" / "main.py").is_file() and not list(other.rglob("*.pyc"))


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
                "DLLs/tcl86t.dll", "DLLs/py.ico", "DLLs/_msi.pyd", "tcl/tcl8.6/init.tcl", "tcl/tk8.6/demos/widget",
                "tcl/dde1.4/tcldde14.dll", "tcl/tcl86t.lib", "Lib/os.py", "Lib/test/x.py"):
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n")
    dst.mkdir()
    rt.copy_python_core(dst, base)
    zname = rt.stdlib_zip_name()
    got = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert got == sorted(["python312.dll", "python3.dll", "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll",
                          "tcl/tcl8.6/init.tcl", zname, Path(zname).stem + "._pth"])
    # sys.path = stdlib zip, DLLs, the folder itself (packages sit at the top); no `import site`, no user site
    assert (dst / (Path(zname).stem + "._pth")).read_text("utf-8").split() == [zname, "DLLs", "."]
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


@pytest.mark.skipif(rt._gcc() is None, reason="needs a MinGW-w64 gcc + windres on PATH")
def test_launcher_compiles_to_an_x64_exe_whose_version_the_app_layer_stamps(tmp_path):
    exe = tmp_path / "tlwu.exe"
    rt.build_launcher(exe, tmp_path / "work")
    data = exe.read_bytes()
    assert bw.pe_machine(data) == 0x8664 and bw.pe_file_version(data) == (0, 0, 0, 0)
    assert bw.set_exe_version(exe, "0.3.5.0") and bw.pe_file_version(exe.read_bytes()) == (0, 3, 5, 0)
    assert len(exe.read_bytes()) == len(data)


def test_windows_build_runs_for_app_changes_and_skips_the_venv_on_a_cache_hit():
    import yaml

    text = (REPO / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    steps = wf["jobs"]["build"]["steps"]
    filt = next(str(s["run"]) for s in steps if s.get("id") == "changes")
    pattern = re.search(r"grep -Eq '([^']+)'", filt).group(1)
    for changed in ("tinglongwu/cli.py", "tinglongwu/gui/app.py", "tlwu_app/main.py", "tinglongwu_setup/cli.py",
                    "packaging/build_windows.py", "requirements.txt"):
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
    for f in ("packaging/build_windows.py", "packaging/runtime_layer.py"):
        code = (REPO / f).read_text("utf-8")
        assert '"-m", "nuitka"' not in code and "run_nuitka" not in code, f
    assert not (REPO / "packaging" / "tlwu_entry.py").exists()
    assert bw.APP_PACKAGES == rt.APP_PACKAGES
