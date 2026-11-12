import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "scripts" / "eval_talk.py"
_spec = importlib.util.spec_from_file_location("eval_talk", _p)
et = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(et)

LOG = """latency: asr 800 ms for 7.9 s of audio (0 line(s) waiting for MT)
latency: mt 200 ms (waited 10 ms in queue)
latency: asr 1200 ms for 8.0 s of audio (0 line(s) waiting for MT)
latency: mt 400 ms (en-zh 400 ms) (waited 0 ms in queue)
"""


def test_wer_on_tiny_fixture():
    ref = "[00:01] the quick brown fox\n[00:05] jumps over the lazy dog"
    r = et.build_report(ref, ["The quick brown fox jumps over a lazy dog."], "", {})
    assert r["ref_words"] == 9 and r["wer_errors"] == 1
    assert r["wer_x100"] == 11.11


def test_boundary_duplicates_and_cuts():
    lines = ["We went to the old town square.", "the old town square and then home", "It ended"]
    assert et.boundary_duplicates(lines) == 1
    assert et.mid_clause_cuts(lines) == 1  # line 2 has no end punctuation; the last line is never counted
    assert et.boundary_duplicates(["a b c.", "d e f."]) == 0


def test_latency_log_parse_and_percentiles():
    asr, mt, lat = et.parse_latency_log(LOG)
    assert asr == [800, 1200] and mt == [200, 400] and lat == [1.01, 1.6]
    r = et.build_report("hello world", ["hello world"], LOG, {"mt": "X"})
    assert r["latency_p50_s"] == 1.01 and r["latency_p90_s"] == 1.6
    assert r["asr_ms_per_chunk"] == 1000.0 and r["mt_ms_per_chunk"] == 300.0 and r["engines"] == {"mt": "X"}
    json.dumps(r)


def test_cli_help_states_no_media():
    out = subprocess.run([sys.executable, str(_p), "--help"], capture_output=True, text=True, check=True).stdout
    assert "--audio" in out and "NO EVAL MEDIA" in out
