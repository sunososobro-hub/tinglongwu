"""0.3.8: 標示說話者 toggle: persistence, lazy load, on-demand prompt (downloads and sherpa import mocked)."""
from tinglongwu.gui import speaker_pack as sp
from tinglongwu.gui.core import DEFAULTS, load_settings, save_settings

MISSING = dict(pkg_ok=lambda: False, model_ok=lambda d: False)
PRESENT = dict(pkg_ok=lambda: True, model_ok=lambda d: True)


def test_toggle_persists_default_off(tmp_path):
    p = tmp_path / "gui.json"
    assert DEFAULTS["speaker_id"] is False and load_settings(p)["speaker_id"] is False
    s = load_settings(p)
    s["speaker_id"] = True
    save_settings(s, p)
    assert load_settings(p)["speaker_id"] is True


def test_off_means_no_labeler_even_if_installed(tmp_path):
    calls = []
    assert sp.make_labeler({"speaker_id": False}, tmp_path, factory=lambda *a, **k: calls.append(1)) is None
    assert calls == []


def test_on_and_present_creates_labeler(tmp_path):
    lab = object()
    got = sp.make_labeler({"speaker_id": True}, tmp_path, factory=lambda m, on_ready=None: lab)
    assert got is lab
    assert sp.on_toggle(True, tmp_path, lambda *a: 1 / 0, lambda: 1 / 0, **PRESENT) == "on"


def test_on_and_missing_prompts_with_real_size_then_downloads(tmp_path):
    asked, started = [], []

    def ask(text, ok, no):
        asked.append(text)
        return True

    r = sp.on_toggle(True, tmp_path, ask, lambda: started.append(1), ui_lang="zh-TW", frozen=False,
                     pytag="cp312", **MISSING)
    assert r == "downloading" and started == [1]
    assert "47 MB" in asked[0] and asked[0].startswith("要下載多人分辨")  # 2.18 + 16.66 + 28.28


def test_frozen_size_is_full(tmp_path):
    assert sp.size_mb(tmp_path, frozen=True, pytag="cp312", **MISSING) == 47


def test_decline_goes_back_off(tmp_path):
    started = []
    assert sp.on_toggle(True, tmp_path, lambda *a: False, lambda: started.append(1), **MISSING) == "off"
    assert started == []
    assert sp.on_toggle(False, tmp_path, lambda *a: 1 / 0, lambda: 1 / 0) == "off"


def test_download_runs_pip_then_model_and_reports_failure(tmp_path):
    seen, evs = [], []

    def runner(pack, put, mdir=None, cmd=None):
        seen.append(cmd)
        put(("pack_line", "x"))
        put(("pack_done", pack, 0))
        return 0 if len(seen) == 1 else 5

    rc = sp.run_download(evs.append, tmp_path, "req.txt", frozen=False, runner=runner, **MISSING)
    assert rc == 5 and "pip" in seen[0] and "--speaker-model" in seen[1]
    assert evs[-1] == ("pack_done", "speaker", 5) and sum(e[0] == "pack_done" for e in evs) == 1
    seen.clear()
    sp.run_download(lambda e: None, tmp_path, "req.txt", frozen=True, runner=runner, **MISSING)
    assert len(seen) == 1 and "--speaker-model" in seen[0] and "--speaker-pylib" in seen[0]  # packaged: no pip


def _wheel(path, files):
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)


def test_pylib_download_verifies_hash_and_unpacks(tmp_path):
    import hashlib
    import io

    import pytest

    from tinglongwu_setup import downloader as dl

    src = tmp_path / "src"
    src.mkdir()
    _wheel(src / "a.whl", {"sherpa_onnx/__init__.py": "x=1", "sherpa_onnx/lib/a.dll": "d"})
    _wheel(src / "c.whl", {"sherpa_onnx_core/b.txt": "c"})
    pin = lambda f, base="": (f, (src / f).stat().st_size, hashlib.sha256((src / f).read_bytes()).hexdigest(), "http://x/")  # noqa: E731

    def opener(url, timeout):
        return io.BytesIO((src / url.rsplit("/", 1)[1]).read_bytes())

    md = tmp_path / "models"
    dl.get_speaker_pylib(md, opener=opener, tag="cp312", wheels={"cp312": pin("a.whl")}, core=pin("c.whl"))
    assert (md / "speaker/pylib/sherpa_onnx/__init__.py").is_file() and (md / "speaker/pylib/sherpa_onnx_core/b.txt").is_file()
    bad = list(pin("a.whl"))
    bad[2] = "0" * 64
    md2 = tmp_path / "m2"
    with pytest.raises(SystemExit):
        dl.get_speaker_pylib(md2, opener=opener, tag="cp312", wheels={"cp312": tuple(bad)}, core=pin("c.whl"))
    assert not (md2 / "speaker/pylib/sherpa_onnx").exists()
    with pytest.raises(SystemExit):  # no pinned wheel for this Python
        dl.get_speaker_pylib(md2, opener=opener, tag="cp399")


def test_pylib_activate_inserts_path_only_when_on(tmp_path, monkeypatch):
    import sys

    from tinglongwu import speaker_pylib

    d = tmp_path / "speaker" / "pylib"
    d.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", list(sys.path))
    # toggle off: make_labeler returns before any import / path change
    before = list(sys.path)
    assert sp.make_labeler({"speaker_id": False}, tmp_path, factory=lambda *a, **k: 1 / 0) is None
    assert sys.path == before
    assert speaker_pylib.activate(tmp_path) is True and sys.path[0] == str(d)
    assert speaker_pylib.activate(tmp_path) is True and sys.path.count(str(d)) == 1
    assert speaker_pylib.activate(tmp_path / "none") is False


def test_import_failure_is_reported(tmp_path, monkeypatch):
    import importlib

    def boom(name, *a, **k):
        raise ImportError("dll")

    monkeypatch.setattr(importlib, "import_module", boom)
    assert sp.import_ok(tmp_path) is False
