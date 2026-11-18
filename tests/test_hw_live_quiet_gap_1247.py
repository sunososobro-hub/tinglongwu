"""#1247: the #894 subtitle gap counts speech time only. Runs 44352160658 / 44370907146 failed at 11.1-11.2 s across a
clip join (line shown 9.9 s, next line 21.0 s for one sentence that ended at 19.7 s): the 3+ s pause between two
FLEURS clips, where nobody speaks, was counted as "speech without a subtitle"."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"
SR = 16000


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks_quiet_1247", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()


def _tone(seconds: float, amp: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t) + amp * 0.05 * rng.standard_normal(len(t))).astype(np.float32)


def _join(parts: list[tuple[float, float, float]], total: float) -> np.ndarray:
    """parts: (start s, seconds, amplitude) of voiced stretches in a silent clip of `total` seconds."""
    x = np.zeros(int(total * SR), np.float32)
    for i, (s, d, amp) in enumerate(parts):
        y = _tone(d, amp, seed=i)
        x[int(s * SR):int(s * SR) + len(y)] = y
    return x


def test_run_44370907146_shape_passes_once_the_clip_join_pause_is_not_counted():
    # clip 0: speech 0.5-9.2 s then trailing silence; clip 1 (joined after GAP_S) has leading silence, speech
    # 12.9-19.7 s. Lines shown at 9.9 s and 21.0 s, as in the run.
    spans = [[0.0, 10.4], [11.4, 21.0]]
    audio = _join([(0.5, 8.7, 0.1), (12.9, 6.8, 0.1)], 22.0)
    quiet = hl.quiet_spans(audio, spans)
    assert quiet[0][0] == pytest.approx(9.2, abs=0.05) and quiet[0][1] == pytest.approx(12.9, abs=0.05)
    assert quiet[1][0] == pytest.approx(19.7, abs=0.05)  # after the last sentence: still waiting for its line
    lines, speech = [5.0, 9.9, 21.0], (0.0, 21.0)
    assert hl.max_gap(lines, *speech) == pytest.approx(11.1)  # the old reading: FAIL against the 10 s limit
    gap = hl.max_gap(lines, *speech, quiet)
    assert gap == pytest.approx(11.1 - (12.9 - 9.9), abs=0.05)  # 19.7 -> 21.0 (latency) still counts
    ok, msg = hl.check_mic(0.11, 0.104, gap, 48, wall_gap=11.1)
    assert ok, msg
    assert "11.1 s wall incl. pauses" in msg


def test_subtitles_stopping_during_continuous_speech_still_fail():
    spans = [[0.0, 30.0]]
    audio = _join([(0.0, 30.0, 0.1)], 30.0)
    quiet = hl.quiet_spans(audio, spans)
    assert quiet == []
    gap = hl.max_gap([5.0, 17.0, 29.0], 0.0, 30.0, quiet)
    assert gap == pytest.approx(12.0)
    ok, msg = hl.check_mic(0.1, 0.1, gap, 3)
    assert not ok and "gap without a subtitle 12.0 s" in msg


def test_short_pauses_inside_speech_still_count():
    # three sentences with 0.6 s pauses (< QUIET_MIN_S): nothing is subtracted
    spans = [[0.0, 20.0]]
    audio = _join([(0.0, 6.0, 0.1), (6.6, 6.0, 0.1), (13.2, 6.8, 0.1)], 20.0)
    assert hl.quiet_spans(audio, spans) == []
    assert hl.max_gap([1.0, 13.0], 0.0, 20.0, hl.quiet_spans(audio, spans)) == pytest.approx(12.0)


def test_quiet_clip_counts_as_speech_not_as_a_pause():
    # clip 1 is ~40 dB quieter than clip 0 (FLEURS clips at -61..-66 dBFS, #1247): measured per clip, it is speech
    spans = [[0.0, 5.0], [6.0, 16.0]]
    audio = _join([(0.0, 5.0, 0.3), (6.0, 10.0, 0.003)], 16.0)
    quiet = hl.quiet_spans(audio, spans)
    assert quiet == [(5.0, 6.0)]
    assert hl.max_gap([4.0, 15.5], 0.0, 16.0, quiet) == pytest.approx(10.5)  # 11.5 s minus the 1 s join only


def test_a_pause_still_running_when_the_line_comes_is_waiting_time():
    # speech ends at 10 s, its line comes at 14 s: the 4 s wait counts even though nobody speaks
    assert hl.max_gap([0.5, 14.0], 0.0, 14.0, [(10.0, 14.0)]) == pytest.approx(13.5)


def test_gap_span_picks_the_longest_speech_gap_not_the_longest_wall_gap():
    quiet = [(2.0, 9.0)]
    # wall: [0, 14] = 14 s but only 7 s of speech; [14, 25] = 11 s, all speech
    assert hl.gap_span([14.0], 0.0, 25.0, quiet) == (14.0, 25.0)
    assert hl.gap_span([14.0], 0.0, 25.0) == (0.0, 14.0)
