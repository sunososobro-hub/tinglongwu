"""#1741 speak-mode CPU readiness report: clip building, run collection and the numbers in the report."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from tlwu_eval import speak_ready as sr


def test_script_has_20_sentences_and_gaps_in_range_and_seeded():
    assert len(sr.SCRIPT) >= 20
    g = sr.gaps(len(sr.SCRIPT))
    assert g == sr.gaps(len(sr.SCRIPT))  # same clip every run / job
    assert all(1.5 <= x <= 2.0 for x in g)


def test_join_places_parts_between_pauses():
    a, b = np.ones(1600, np.float32), np.ones(3200, np.float32)
    x = sr.join([a, b], [1.5, 2.0], lead=1.0, tail=3.0)
    assert len(x) == int((1.0 + 0.1 + 1.5 + 0.2 + 2.0 + 3.0) * sr.SR)
    assert x[:16000].max() == 0 and x[16000] == 1 and x[16000 + 1600] == 0


def test_noise_clip_is_silence_then_keyboard(tmp_path):
    import soundfile as sf

    x, rate = sf.read(str(sr.noise_clip(tmp_path / "n.wav")), dtype="float32")
    assert rate == sr.SR and len(x) == int(120 * sr.SR)
    assert np.abs(x[: 60 * sr.SR]).max() == 0 and np.abs(x[60 * sr.SR:]).max() > 0.05


def test_speak_cmd_is_paced_no_play_with_latency_records_and_parses(tmp_path):
    from tinglongwu.reverse.args import build_parser

    a = SimpleNamespace(kokoro_python="py", kokoro_model="km", models_dir="md", whisper=None)
    cmd = sr.speak_cmd(tmp_path / "c.wav", tmp_path / "l.jsonl", tmp_path / "r.json", a)
    p = build_parser().parse_args(cmd[3:])
    assert p.paced and p.no_confirm and p.no_play and p.to == "en" and p.latency_jsonl.endswith("l.jsonl")
    assert p.report.endswith("r.json") and p.models_dir == "md" and p.tts_engine == "kokoro"


def test_run_one_collects_records_and_items(tmp_path, monkeypatch):
    fake = tmp_path / "fake.py"
    fake.write_text(
        "import json, sys\n"
        "a = sys.argv\n"
        "open(a[a.index('--latency-jsonl') + 1], 'w').write(json.dumps({'total_first_audio': 2.5}) + '\\n')\n"
        "json.dump({'items': [{'zh': '你好', 'en': 'Hello', 'decision': 'sent'}]},"
        " open(a[a.index('--report') + 1], 'w'))\n", encoding="utf-8")
    monkeypatch.setattr(sr, "speak_cmd", lambda w, j, r, a: [sys.executable, str(fake), "--latency-jsonl", str(j),
                                                             "--report", str(r)])
    out = sr.run_one(tmp_path / "x.wav", tmp_path, "t", SimpleNamespace(timeout=60))
    assert out["rc"] == 0 and out["records"] == [{"total_first_audio": 2.5}] and out["items"][0]["en"] == "Hello"


def _run(fa, items):
    return {"rc": 0, "records": [{"total_first_audio": v} for v in fa], "items": items}


def test_summarize_latency_percentiles_chrf_and_noise():
    script = (("甲", "Good morning everyone."), ("乙", "Thank you."))
    good = [{"decision": "sent", "en": "Good morning everyone."}, {"decision": "sent", "en": "Thank you."}]
    bad = [{"decision": "sent", "en": "Something else entirely."}, {"decision": "no_audio", "en": "Thank you."}]
    runs = [_run([1.0, 2.0, None], good), _run([3.0, 10.0], bad)]
    noise = _run([], [{"decision": "sent", "zh": "請不吝點贊訂閱轉發打賞支持明鏡與點點欄目", "en": "x"},
                      {"decision": "sent", "zh": "我們開會吧", "en": "y"}])
    s = sr.summarize(runs, noise, script)
    assert s["first_audio_s"] == {"n": 4, "p50": 2.0, "p95": 10.0, "max": 10.0}
    assert s["per_run"][0]["chrf"] == 100.0 and s["per_run"][1]["chrf"] < 30
    assert s["per_run"][1]["sent"] == 1 and s["per_run"][1]["lines"] == 2
    assert s["noise"]["sent"] == 2 and s["noise"]["held"] == 1
    md = sr.markdown(s, "ubuntu")
    assert "p50 2.0 s, p95 10.0 s" in md and "2 lines sent (target 0)" in md and "| 2 | 1/2 |" in md


def test_summarize_with_no_lines_does_not_crash():
    s = sr.summarize([_run([], [])], _run([], []))
    assert s["first_audio_s"]["p50"] is None and s["noise"]["sent"] == 0
    assert "p50 None" in sr.markdown(s)


@pytest.mark.parametrize("zh,flag", [("請不吝點贊訂閱轉發打賞支持明鏡與點點欄目", True), ("我們開會吧", False)])
def test_held_uses_the_send_mode_hallucination_guard(zh, flag):
    assert sr.held(zh) is flag


def test_main_writes_json_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "script_clip", lambda out, p, m: out)
    monkeypatch.setattr(sr, "noise_clip", lambda out: out)
    monkeypatch.setattr(sr, "run_one", lambda w, work, tag, a: _run([1.5], [{"decision": "sent", "en": "Hi."}]))
    rc = sr.main(["--runs", "2", "--kokoro-python", "p", "--kokoro-model", "m", "--json", str(tmp_path / "o.json"),
                  "--md", str(tmp_path / "o.md")])
    assert rc == 0
    assert json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["runs"] == 2
    assert "speak readiness" in (tmp_path / "o.md").read_text(encoding="utf-8")


def _fake_child(tmp_path, monkeypatch, code: str):
    fake = tmp_path / "child.py"
    fake.write_text(code, encoding="utf-8")
    monkeypatch.setattr(sr, "speak_cmd", lambda w, j, r, a: [sys.executable, str(fake)])


def test_run_one_timeout_is_an_error_not_an_exception(tmp_path, monkeypatch):
    _fake_child(tmp_path, monkeypatch, "import time; time.sleep(30)\n")
    out = sr.run_one(tmp_path / "x.wav", tmp_path, "t", SimpleNamespace(timeout=1))
    assert out["rc"] != 0 and out["error"] == "timeout after 1s" and out["records"] == []


def test_run_one_silent_nonzero_exit_still_reports_an_error(tmp_path, monkeypatch):
    _fake_child(tmp_path, monkeypatch, "import sys; sys.exit(3)\n")
    out = sr.run_one(tmp_path / "x.wav", tmp_path, "t", SimpleNamespace(timeout=60))
    assert out["error"] == "exit code 3"
    assert sr.summarize([out], _run([], []))["errors"] == ["exit code 3"]
