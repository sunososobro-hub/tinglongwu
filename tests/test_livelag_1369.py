"""#1369 cloud verify: latency records carry when each line was shown ("at"), and scripts/eval_livelag.py turns
them into the translated-subtitle lag at 60 s / 180 s with the Acceptance bounds (<= 8 s, growth <= 2 s) and the
music-only check. The real-model run with a busy core is the eval.yml low-end-cpu step."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from tinglongwu.capture.base import SAMPLE_RATE
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.stream import PacedSource

from test_latency_record import SPEECH_S, TOL, _Asr, _Clip, _Mt, _Sink, _Stamped, _vad

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_livelag as ll  # noqa: E402


def test_record_has_shown_time_since_start():
    clip, sink, records = _Clip(), _Sink(), []
    seg = Segmenter(chunk=10.0, use_vad=True, step=0.25, vad_fn=_vad)
    t0 = time.monotonic()
    run(_Stamped(PacedSource(clip), clip), seg, _Asr(), _Mt(), sink, "zh", merger=None, audio_clock=False,
        on_record=records.append)
    assert len(records) == 1
    rec = records[0]
    assert rec["at"] == pytest.approx(sink.lines[0][0] - t0, abs=TOL)
    # at - total is the end of speech on the same clock: the audio position of the line (0.5 s into the clip)
    assert rec["at"] - rec["total"] == pytest.approx(SPEECH_S, abs=TOL)


def _recs(delay):
    """One translated line per 3 s segment, shown `delay(eos)` seconds after its speech ended."""
    out = []
    for i in range(60):
        eos = 3.0 * (i + 1)
        out.append({"at": eos + delay(eos), "total": delay(eos), "text": f"line {i}.", "mt_skipped": False})
    return out


def test_steady_lag_passes():
    res = ll.score(_recs(lambda e: 4.0))
    assert res["pass"], res
    assert 4.0 <= res["lag_60_s"] <= 7.0 and abs(res["lag_180_s"] - res["lag_60_s"]) < 0.5


def test_growing_lag_fails_like_the_field_test():
    res = ll.score(_recs(lambda e: 2.0 + 0.3 * e))  # the old 30 s hold: further behind every second
    assert not res["pass"]
    assert any("grew" in f for f in res["fails"]), res["fails"]


def test_lag_over_8_s_at_60_fails():
    res = ll.score(_recs(lambda e: 12.0))
    assert not res["pass"] and any("lag at 60 s" in f for f in res["fails"])


def test_untranslated_catchup_lines_do_not_count_as_translated():
    recs = _recs(lambda e: 4.0)
    for r in recs[10:]:
        r["mt_skipped"] = True  # only the first 30 s got translated
    res = ll.score(recs)
    assert not res["pass"] and res["lag_180_s"] > 140


def test_music_only_line_fails():
    recs = _recs(lambda e: 4.0)
    recs[20]["text"] = "Music. Music."
    res = ll.score(recs)
    assert not res["pass"] and res["music_lines"] == ["Music. Music."]
    assert "music-only" in ll.report(res)


def test_no_translated_line_fails():
    assert not ll.score([])["pass"]


def test_score_cli_exit_codes(tmp_path, capsys):
    good, bad = tmp_path / "g.jsonl", tmp_path / "b.jsonl"
    good.write_text("\n".join(json.dumps(r) for r in _recs(lambda e: 3.0)), "utf-8")
    bad.write_text("\n".join(json.dumps(r) for r in _recs(lambda e: 1.0 + 0.3 * e)), "utf-8")
    assert ll.main(["score", "--jsonl", str(good)]) == 0
    assert "PASS" in capsys.readouterr().out
    assert ll.main(["score", "--jsonl", str(bad)]) == 1


def test_build_makes_a_3_min_clip_with_music(tmp_path):
    sf = pytest.importorskip("soundfile")
    data = tmp_path / "smoke"
    data.mkdir()
    rng = np.random.default_rng(1)
    for i in range(2):
        sf.write(str(data / f"en{i}.wav"), rng.uniform(-0.1, 0.1, 5 * SAMPLE_RATE).astype("float32"), SAMPLE_RATE)
    (data / "manifest.jsonl").write_text("\n".join(json.dumps(
        {"id": f"en{i}", "lang": "en", "wav": f"en{i}.wav", "ref_text": "x"}) for i in range(2)), "utf-8")
    out = ll.build(data, tmp_path / "clip.wav")
    audio, sr = sf.read(str(out))
    assert sr == SAMPLE_RATE and len(audio) == int(ll.CLIP_S * SAMPLE_RATE)
