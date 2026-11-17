"""#1385: the GUI says which speech model the chosen Quality runs, in plain words with its download size, and
Accurate on a fresh install downloads the larger model instead of silently keeping the one setup fetched."""
import pytest

from tinglongwu import cli
from tinglongwu.gui import core
from tinglongwu.hwprobe import HwInfo

CPU_PC = HwInfo(gpu=None, vram_mib=None, ram_mib=16384, cores=8)
GPU_PC = HwInfo(gpu="RTX 3050", vram_mib=8192, ram_mib=16384, cores=8)


@pytest.fixture(autouse=True)
def _en(monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "en")


@pytest.fixture
def fresh(tmp_path):
    (tmp_path / "whisper-small").mkdir()  # what the default installer fetches (#1359)
    return tmp_path


def test_accurate_on_fresh_cpu_install_downloads_turbo_on_start(fresh):
    m = core.quality_speech_model("quality", False, CPU_PC, fresh)
    assert m == "large-v3-turbo"
    # before #1385 the Start had no download for non-manual qualities, and the CLI fell back to small
    assert core.missing_start_model({"quality": "quality", "model": "small"}, fresh, want=m) == "large-v3-turbo"
    (fresh / "whisper-large-v3-turbo").mkdir()
    assert core.missing_start_model({"quality": "quality", "model": "small"}, fresh, want=m) is None


def test_auto_and_fast_name_what_the_cli_picks(fresh):
    want = lambda level, dev, hw: ({"auto": "large-v3-turbo", "fast": "base"}[level], "cpu")  # noqa: E731
    assert core.quality_speech_model("auto", False, CPU_PC, fresh, want=want) == "small"  # best downloaded
    assert core.quality_speech_model("fast", False, CPU_PC, fresh, want=want) == "small"
    assert cli.auto_model("auto", "cpu", fresh, hw=CPU_PC, say=lambda *a, **k: None,
                          asr_choice=lambda hw: "small") == "small"


def test_manual_names_the_picked_model(fresh):
    assert core.quality_speech_model("manual", True, GPU_PC, fresh, manual_model="medium") == "medium"
    assert core.missing_start_model({"quality": "manual", "model": "medium"}, fresh, want="small") == "medium"


def test_gpu_accurate_is_large_v3_when_it_fits():
    assert core.quality_speech_model("quality", True, GPU_PC, None) == "large-v3"


def test_hint_text_before_and_after_download():
    before = core.quality_hint("large-v3-turbo", False, downloaded=False)
    assert before == ("Speech model: large-v3-turbo (more accurate, slower on this PC). "
                      "The next Start downloads it first (about 1.7 GB).")
    after = core.quality_hint("large-v3-turbo", True, downloaded=True)
    assert after == "Speech model: large-v3-turbo (balanced speed and accuracy). Downloaded."
    assert "fastest" in core.quality_hint("base", False, True)
    assert "about 486 MB" in core.quality_hint("small", False, False)


def test_hint_keys_exist_in_every_ui_language(monkeypatch):
    from tinglongwu import i18n

    keys = [k for k in i18n.MESSAGES["en"] if k.startswith("gui_asr_")]
    assert len(keys) == 8
    for lang in ("ru", "zh-TW", "ja"):
        assert all(k in i18n.MESSAGES[lang] for k in keys), lang


def test_want_model_matches_auto_model_target(fresh):
    assert cli.want_model("quality", "cpu", CPU_PC) == ("large-v3-turbo", "cpu")
    assert cli.want_model("quality", "cuda", GPU_PC) == ("large-v3", "gpu")
