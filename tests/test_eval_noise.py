"""#121: silence / ambient-noise hallucination test in the eval smoke."""
import json
from types import SimpleNamespace

import numpy as np

from tlwu_eval import noise
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock, fake_engines, make_data


class Asr:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def transcribe(self, audio):
        assert audio.dtype == np.float32
        self.calls += 1
        return SimpleNamespace(text=self.text, lang="en") if self.text else None


def test_four_categories_are_synthesised_deterministically():
    clips = noise.build_clips()
    assert [c["category"] for c in clips] == list(noise.CATEGORIES)
    assert not clips[0]["audio"].any() and clips[1]["audio"].any() and clips[3]["audio"].any()
    assert np.array_equal(noise.synth("aircon", seed=1), noise.synth("aircon", seed=1))


def test_quiet_asr_is_green():
    rep = noise.run_noise(Asr(""), noise.build_clips())
    assert rep["count"] == 0 and rep["stage"] == "PASS" and rep["total"] == 4


def test_text_on_noise_is_counted_per_category_with_the_text():
    rep = noise.run_noise(Asr("我今天在屋內"), noise.build_clips())
    assert rep["count"] == 4 and rep["rate"] == 100.0 and rep["stage"] == "FAIL"
    assert rep["categories"]["keyboard"]["texts"][0]["text"] == "我今天在屋內"


def test_stock_phrase_removed_by_pipeline_filter_is_not_counted():
    assert noise.run_noise(Asr("Thank you."), noise.build_clips())["count"] == 0  # quiet clip: filter drops it


def test_real_recordings_are_added(tmp_path):
    import soundfile as sf
    (tmp_path / "keyboard").mkdir()
    sf.write(tmp_path / "keyboard" / "k1.wav", np.zeros(16000, np.float32), 16000)
    ids = [c["id"] for c in noise.build_clips(tmp_path)]
    assert "keyboard_k1" in ids and len(ids) == 5


def test_smoke_cli_goes_red_on_noise_hallucination(tmp_path, capsys):
    data = make_data(tmp_path, langs=("en",))
    out = tmp_path / "r.json"
    args = ["--smoke", "--data", str(data), "--out", str(out), "--nhk-dir", str(tmp_path / "x"),
            "--noise-dir", str(tmp_path / "x")]
    clock = Clock()
    eng = fake_engines(clock)
    # fake ASR echoes the reference for every 2 s clip; noise clips are 10 s, so assert on them directly
    class Noisy:
        def __init__(self, lang): pass
        def transcribe(self, audio):
            clock.t += 0.1
            return SimpleNamespace(text="we will be right back" if len(audio) == 160000 else "the cat sat on the mat", lang="en")
    eng.load_asr = Noisy
    assert main(args, engines=eng, totals=(16000, None), clock=clock) == 1
    printed = capsys.readouterr().out
    assert "noise silence: 1/1 (100.00) FAIL" in printed
    rep = json.loads(out.read_text("utf-8"))
    assert rep["noise"]["count"] == 4 and rep["score"]["metrics"]["hallucination"]["score"] == 0
    clock = Clock()
    eng2 = fake_engines(clock)
    assert main(args + ["--no-noise"], engines=eng2, totals=(16000, None), clock=clock) == 0
