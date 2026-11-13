import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "scripts" / "eval_talk.py"
_spec = importlib.util.spec_from_file_location("eval_talk", _p)
et = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(et)

NL = chr(10)
# #435: a fixed --latency-jsonl file (what `tlwu --file X --paced --latency-jsonl PATH` writes)
RECORDS = [
    {"vad": 0.25, "asr": 0.8, "merge": 0.0, "mt": 0.3, "total": 1.35, "t_source_shown": None, "lang": "en",
     "text": "hello world."},
    {"vad": 0.25, "asr": 1.2, "merge": 1.5, "mt": 0.4, "total": 3.35, "t_source_shown": None, "lang": "en",
     "text": "the quick brown fox."},
    {"vad": 0.5, "asr": 0.9, "merge": 0.1, "mt": 0.2, "total": 1.7, "t_source_shown": None, "lang": "en",
     "text": "jumps over."},
    {"vad": 0.25, "asr": 1.0, "merge": 0.0, "mt": 0.5, "total": 1.75, "t_source_shown": None, "lang": "en",
     "text": "the lazy dog."},
]


def test_wer_on_tiny_fixture():
    ref = "[00:01] the quick brown fox\n[00:05] jumps over the lazy dog"
    r = et.build_report(ref, [{"text": "The quick brown fox jumps over a lazy dog.", "total": 1.0}], {})
    assert r["ref_words"] == 9 and r["wer_errors"] == 1
    assert r["wer_x100"] == 11.11


def test_boundary_duplicates_and_cuts():
    lines = ["We went to the old town square.", "the old town square and then home", "It ended"]
    assert et.boundary_duplicates(lines) == 1
    assert et.mid_clause_cuts(lines) == 1  # line 2 has no end punctuation; the last line is never counted
    assert et.boundary_duplicates(["a b c.", "d e f."]) == 0


def test_latency_jsonl_aggregation_exact(tmp_path):
    f = tmp_path / "latency.jsonl"
    f.write_text("".join(json.dumps(r) + NL for r in RECORDS) + NL, encoding="utf-8")
    recs = et.read_records(f)
    assert recs == RECORDS
    s = et.latency_summary(recs)
    # totals sorted: 1.35 1.7 1.75 3.35 -> nearest rank p50 = 2nd, p90 = 4th; median = (1.7 + 1.75) / 2
    assert s["latency_p50_s"] == 1.7 and s["latency_p90_s"] == 3.35 and s["latency_median_s"] == 1.725
    assert s["stage_median_s"] == {"vad": 0.25, "asr": 0.95, "merge": 0.05, "mt": 0.35}
    r = et.build_report("hello world the quick brown fox jumps over the lazy dog", recs, {"preset": "meeting"})
    assert r["lines"] == 4 and r["wer_errors"] == 0 and r["latency_p90_s"] == 3.35
    json.dumps(r)


def test_run_tlwu_uses_native_paced_latency_jsonl(tmp_path):
    """The harness runs tlwu itself with --paced --latency-jsonl and reads the records it wrote."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        out = cmd[cmd.index("--latency-jsonl") + 1]
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(RECORDS[0]) + NL)
        return subprocess.CompletedProcess(cmd, 0)

    a = argparse.Namespace(audio="talk.wav", to="zh", models_dir=None)
    recs, engines = et.run_tlwu(a, run=fake_run)
    cmd = seen["cmd"]
    assert cmd[1:3] == ["-m", "tinglongwu"] and "--paced" in cmd and "--debug-latency" not in cmd
    assert cmd[cmd.index("--file") + 1] == str(Path("talk.wav").resolve())
    assert recs == [RECORDS[0]] and "--paced" in engines["cmd"]


def test_run_tlwu_failure_exits(tmp_path):
    a = argparse.Namespace(audio="talk.wav", to="zh", models_dir=None)
    try:
        et.run_tlwu(a, run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2))
    except SystemExit as e:
        assert "exited with 2" in str(e)
    else:
        raise AssertionError("no SystemExit")


def test_cli_help_states_no_media():
    out = subprocess.run([sys.executable, str(_p), "--help"], capture_output=True, text=True, check=True).stdout
    assert "--audio" in out and "NO EVAL MEDIA" in out
