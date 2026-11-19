import importlib.util
import math
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location("silence_floor_ab", Path(__file__).resolve().parents[1] / "scripts/bench/silence_floor_ab.py")
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)


class T:
    def __init__(self, text, no_speech=0.0):
        self.text, self.no_speech = text, no_speech


def test_scale_and_synth_hit_target_level():
    for kind in ("noise", "hum", "music"):
        x = ab.synth(kind, -63.0, seconds=2)
        assert abs(20 * math.log10(ab.rms(x)) + 63.0) < 0.1
    assert np.array_equal(ab.synth("noise", -60, 1, seed=3), ab.synth("noise", -60, 1, seed=3))


def test_floor_decides_what_reaches_asr():
    quiet = ab.synth("noise", -70.0, seconds=1)  # -70 dBFS: below -60 floor, above -80 floor
    assert not ab.reaches_asr(quiet, 1e-3)
    assert ab.reaches_asr(quiet, 1e-4)
    calls = []

    def asr(c):
        calls.append(1)
        return T("some invented line")

    assert ab.count_false_lines([quiet], 1e-3, asr) == 0 and not calls
    assert ab.count_false_lines([quiet], 1e-4, asr) == 1


def test_hallucination_filter_lines_not_counted_and_speech_stats():
    c = ab.synth("hum", -60.0, seconds=1)
    assert ab.count_false_lines([c], 1e-4, lambda x: T("Субтитры сделал DimaTorzok")) == 0
    assert ab.speech_stats([c, c], 1e-4, lambda x: T("one two three")) == (2, 6)
    assert ab.speech_stats([c], 1e-1, lambda x: T("one")) == (0, 0)
    assert "false lines/min" in ab.table([("x", 1e-4, 1, None, 0.5)])


def test_empty_speech_dir_is_an_error(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        ab.speech_clips(tmp_path, 5)
