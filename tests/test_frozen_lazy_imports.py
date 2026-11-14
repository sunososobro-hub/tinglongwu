"""#310: the frozen build missed filelock._soft (loaded lazily by filelock.__getattr__), so the first-run download
crashed. `tlwu.exe --smoke` now resolves those lazy attributes and the Windows build runs it before zipping."""
import io
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
import build_windows  # noqa: E402

from tlwu_app import smoke, tiers  # noqa: E402


@pytest.fixture
def broken_lazy_pkg(tmp_path, monkeypatch):
    """A package like filelock 4.x whose `SoftFileLock` lives in a submodule the (frozen) build left out."""
    pkg = tmp_path / "lazylock"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent('''
        from importlib import import_module
        _LAZY_ATTRIBUTES = {"SoftFileLock": "._soft", "FileLock": "._real"}
        def __getattr__(name):
            mod = _LAZY_ATTRIBUTES.get(name)
            if mod is None:
                raise AttributeError(name)
            return getattr(import_module(mod, __name__), name)
    '''))
    (pkg / "_real.py").write_text("class FileLock: pass\n")  # _soft.py missing, as in the user's build
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "lazylock"
    for m in [m for m in sys.modules if m.startswith("lazylock")]:
        del sys.modules[m]


def test_missing_lazy_submodule_fails_the_smoke(broken_lazy_pkg):
    out = io.StringIO()
    assert smoke.check(modules=[], out=out, lazy=[(broken_lazy_pkg, None, True)]) == 1
    text = out.getvalue()
    assert "FAIL lazylock.SoftFileLock: ModuleNotFoundError: No module named 'lazylock._soft'" in text
    assert "OK   lazylock.FileLock" in text
    assert text.rstrip().endswith("smoke: FAIL (1 required module(s) failed)")


def test_optional_lazy_failure_is_only_a_warning(broken_lazy_pkg):
    out = io.StringIO()
    assert smoke.check_lazy([(broken_lazy_pkg, ("SoftFileLock",), False)], out) == 0
    assert "WARN lazylock.SoftFileLock" in out.getvalue()


def test_real_filelock_and_huggingface_hub_resolve_in_a_full_install():
    out = io.StringIO()
    assert smoke.check_lazy(out=out) == 0, out.getvalue()
    text = out.getvalue()
    for name in ("filelock.SoftFileLock", "huggingface_hub.snapshot_download", "huggingface_hub.utils"):
        assert f"OK   {name} " in text


def test_default_smoke_includes_the_lazy_download_imports(monkeypatch):
    monkeypatch.setattr(smoke, "MODULES", [])
    out = io.StringIO()
    smoke.check(out=out)
    assert "filelock.SoftFileLock" in out.getvalue() and "huggingface_hub.HfApi" in out.getvalue()


def test_build_ships_every_filelock_lazy_submodule(tmp_path):
    """#923: no import graph decides what ships; the runtime layer copies the installed filelock folder whole."""
    import filelock
    import runtime_layer

    lazy = getattr(filelock, "_LAZY_ATTRIBUTES", {})
    site = Path(filelock.__file__).resolve().parent.parent
    (tmp_path / "layer").mkdir()
    shutil.copytree(site / "filelock", tmp_path / "site" / "filelock")
    assert runtime_layer.copy_site_packages(tmp_path / "layer", tmp_path / "site") == ["filelock"]
    runtime_layer.compile_sourceless(tmp_path / "layer")
    for sub in {m.lstrip(".") for m in lazy.values() if m.startswith(".")} | {"_soft"}:
        mod = tmp_path / "layer" / "filelock" / sub.replace(".", "/")
        assert mod.with_suffix(".pyc").is_file() or (mod / "__init__.pyc").is_file(), sub


def test_build_stops_before_zip_when_exe_smoke_fails(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "FAIL filelock.SoftFileLock: ModuleNotFoundError\n", "")

    with pytest.raises(SystemExit, match=r"--smoke failed \(exit 1\)"):
        build_windows.smoke(tmp_path, run=run)
    assert calls == [[str(tmp_path / "tlwu.exe"), "--smoke"]]
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "smoke: OK\n", "")  # noqa: E731
    build_windows.smoke(tmp_path, run=ok)


@pytest.mark.parametrize("tier", [tiers.T_GPU, tiers.T_MIN, tiers.T_CPU])
def test_first_run_tiers_download_hymt_not_qwen(tier):
    args = tier.downloader_args()
    assert "--hymt" in args and not any("qwen" in a.lower() for a in args)
    assert "Qwen" not in tier.label_zh
