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


def test_frozen_size_is_model_only(tmp_path):
    assert sp.size_mb(tmp_path, frozen=True, **MISSING) == 28


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
    assert len(seen) == 1 and "--speaker-model" in seen[0]  # packaged build: no pip
