"""#131: Whisper real-time factor in the speed probe; the CPU tier steps the speech model down when it is too slow,
and `--quality auto` (CLI and GUI default) uses the remembered choice. Fake models + injected clock, no download."""
from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf

from tinglongwu import cli, speedprobe
from tinglongwu.hwprobe import HwInfo, recommend

CPU = HwInfo(None, None, 16000, 8)
GPU = HwInfo("RTX 3050", 8192, 16000, 8)
ALL = ["tiny", "base", "small", "medium", "large-v3-turbo"]


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "clip.wav"
    sf.write(str(p), np.zeros(32000, dtype="float32"), 16000)  # 2 s
    return p


class Fake:
    """transcribe() advances the fake clock by rtf[model] * clip length; records the models loaded."""

    def __init__(self, rtf, mt_s=0.5):
        self.rtf, self.mt_s, self.now, self.loaded = rtf, mt_s, 0.0, []

    def __call__(self):
        return self.now

    def make_asr(self, models_dir, size):
        self.loaded.append(size)

        def transcribe(audio):
            self.now += self.rtf[size] * len(audio) / 16000
        return transcribe

    def make_mt(self, models_dir, src, tgt):
        def tr(s):
            self.now += self.mt_s
        return tr


def run(f, clip, path, hw=CPU, installed=ALL, **kw):
    return speedprobe.run(hw, recommend(hw), "m", path=path, make=f.make_mt, make_asr=f.make_asr, clock=f,
                          clip=clip, installed=installed, **kw)


def test_rtf_above_limit_steps_the_speech_model_down(tmp_path, clip):
    f = Fake({"small": 1.4, "base": 0.4, "tiny": 0.1})
    r = run(f, clip, tmp_path / "sp.json")
    assert (r.asr, f.loaded) == ("base", ["small", "base"])  # never tries a larger model than the tier's
    assert r.asr_rtf == pytest.approx(0.4) and "speech model base" in r.note
    assert speedprobe.apply(recommend(CPU), r).asr == "base"
    saved = json.loads((tmp_path / "sp.json").read_text("utf-8"))
    assert saved["asr"] == "base" and saved["asr_rtf"] == pytest.approx(0.4)


def test_fast_machine_keeps_the_tier_model(tmp_path, clip):
    f = Fake({"small": 0.3, "base": 0.1, "tiny": 0.05})
    r = run(f, clip, tmp_path / "sp.json")
    assert (r.asr, f.loaded) == ("small", ["small"])
    assert speedprobe.apply(recommend(CPU), r).asr == "small"


def test_only_installed_models_and_slowest_kept_when_none_is_real_time(tmp_path, clip):
    f = Fake({"small": 2.0, "base": 1.5, "tiny": 0.9})
    r = run(f, clip, tmp_path / "sp.json", installed=["small", "base"])
    assert (r.asr, f.loaded) == ("base", ["small", "base"]) and r.asr_rtf == pytest.approx(1.5)


def test_asr_budget_stops_stepping(tmp_path, clip):
    f = Fake({"small": 4.0, "base": 2.0, "tiny": 0.2})  # small alone uses 8 s, base 4 s more -> 12 s budget gone
    r = run(f, clip, tmp_path / "sp.json")
    assert f.loaded == ["small", "base"] and r.asr == "base"


def test_no_clip_or_gpu_tier_is_not_timed(tmp_path, clip):
    f = Fake({"small": 9.0})
    r = run(f, tmp_path / "missing.wav", tmp_path / "a.json")
    assert r.asr == "small" and r.asr_rtf is None and f.loaded == [] and "no test clip" in r.note
    assert speedprobe.cpu_asr_choice(CPU, tmp_path / "a.json") is None  # untimed answer is not a choice
    g = Fake({"large-v3-turbo": 9.0})
    r = run(g, clip, tmp_path / "b.json", hw=GPU)
    assert r.asr_rtf is None and g.loaded == []


def test_untimed_cache_is_measured_again_once_the_clip_exists(tmp_path, clip):
    path = tmp_path / "sp.json"
    path.write_text(json.dumps({"hw": speedprobe.signature(CPU), "mt": "opus", "hymt_median_s": 1.5,
                                "samples": 7, "note": "part 1 entry"}), "utf-8")  # written before #131
    f = Fake({"small": 1.2, "base": 0.3})
    r = run(f, clip, path)
    assert not r.cached and r.asr == "base"
    f2 = Fake({"small": 0.1})
    again = run(f2, clip, path)
    assert again.cached and again.asr == "base" and f2.loaded == []


def test_auto_model_on_cpu_uses_remembered_choice(tmp_path, clip):
    path = tmp_path / "sp.json"
    run(Fake({"small": 1.4, "base": 0.4}), clip, path)
    mdir = tmp_path / "models"
    for m in ALL:
        (mdir / f"whisper-{m}").mkdir(parents=True)
    choice = lambda hw: speedprobe.cpu_asr_choice(hw, path)  # noqa: E731
    say = lambda *a, **k: None  # noqa: E731
    assert cli.auto_model("auto", "cpu", mdir, hw=CPU, say=say, asr_choice=choice) == "base"
    assert cli.auto_model("balanced", "cpu", mdir, hw=CPU, say=say, asr_choice=choice) == "base"
    assert cli.auto_model("quality", "cpu", mdir, hw=CPU, say=say, asr_choice=choice) == "large-v3-turbo"
    assert cli.auto_model("auto", "cpu", mdir, hw=CPU, say=say, asr_choice=lambda hw: None) == "small"
    assert cli.auto_model("auto", "cuda", mdir, hw=GPU, say=say, asr_choice=lambda hw: "tiny") == "large-v3-turbo"
    # a remembered model larger than the level's never moves it up
    assert cli.auto_model("fast", "cpu", mdir, hw=CPU, say=say, asr_choice=lambda hw: "small") == "base"
