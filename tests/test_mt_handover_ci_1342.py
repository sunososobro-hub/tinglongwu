"""#1342: the CI hand-over check (scripts/mt_handover_ci.py) - source lines while Hy-MT2 is missing, translated lines
after it lands, same session. Runs the checker against fake tlwu sessions (real processes, real JSONL), not source."""
from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("mt_handover_ci", ROOT / "scripts" / "mt_handover_ci.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

SRC = {"src": "This is a test.", "dst": ""}
TR = {"src": "This is a test.", "dst": "這是一個測試。"}

# A stand-in for `tlwu --mt-pending --save PATH --models-dir M`: one line every 0.2 s; translated once M/landed exists
# (the hand-over without a restart). FAKE_MODE=never: never translates (a build that needs a restart).
# FAKE_MODE=early: translates from the start (the model was not really missing).
FAKE = r'''
import json, os, sys, time
from pathlib import Path
a = sys.argv[1:]
save, mdir = Path(a[a.index("--save") + 1]), Path(a[a.index("--models-dir") + 1])
mode = os.environ.get("FAKE_MODE", "ok")
n_tr = 0
with open(save, "a", encoding="utf-8") as f:
    for i in range(400):
        landed = (mdir / "landed").exists()
        tr = mode == "early" or (mode in ("ok", "crash", "flap", "quit") and landed)
        if mode == "flap" and n_tr >= 1:
            tr = False  # translated once, then back to source
        n_tr += tr
        f.write(json.dumps({"t": i, "src": "This is a test.", "dst": "這是一個測試。" if tr else ""},
                           ensure_ascii=False) + "\n")
        f.flush()
        if mode == "crash" and tr:
            sys.exit(3)
        if mode == "quit" and tr:
            sys.exit(0)  # a clean exit right after the first translated line, inside the settle window
        if mode == "flap" and tr:
            time.sleep(2.5)  # the checker polls (1 s) and sees this translated row alone before the fall-back
        time.sleep(0.2)
'''


def test_judge_handover_passes():
    assert h.judge([SRC, SRC, SRC, TR, TR], 2) == []


def test_judge_no_source_line_first_fails():
    assert any("no source-text line" in p for p in h.judge([TR, TR], 0))
    assert any("no source-text line" in p for p in h.judge([], None))


def test_judge_needs_restart_fails():
    assert any("no translated line after" in p for p in h.judge([SRC, SRC, SRC], 1))


def test_judge_translated_before_landing_fails():
    assert any("before Hy-MT2 landed" in p for p in h.judge([TR, SRC, TR], 2))


def test_judge_falls_back_to_source_fails():
    assert any("not one-way" in p for p in h.judge([SRC, TR, SRC], 1))


def test_judge_lost_source_fails():
    assert any("without source text" in p for p in h.judge([SRC, {"src": "", "dst": "這"}], 1))


def test_long_clip_repeats_to_length(tmp_path):
    out = tmp_path / "long.wav"
    n = h.long_clip(h.CLIP, out, 30)
    with wave.open(str(out)) as w:
        dur = w.getnframes() / w.getframerate()
    assert dur >= 30 and abs(dur - n) < 0.01


def _run(tmp_path, monkeypatch, mode, timeout=20, settle=2):
    fake = tmp_path / "fake_tlwu.py"
    fake.write_text(FAKE, "utf-8")
    mdir = tmp_path / "models"
    mdir.mkdir()
    monkeypatch.setenv("FAKE_MODE", mode)
    # the checker appends `--models-dir M`: argv[2] is the models dir (no path inside the -c program)
    land = f'"{sys.executable}" -c "import pathlib, sys; pathlib.Path(sys.argv[2], \'landed\').touch()"'
    out = tmp_path / "h.json"
    rc = h.main(["--models-dir", str(mdir), "--land-cmd", land, "--tlwu", f'"{sys.executable}" "{fake}"',
                 "--timeout", str(timeout), "--settle", str(settle), "--log", str(tmp_path / "h.log"), "--out", str(out)])
    return rc, json.loads(out.read_text("utf-8"))


def test_run_handover_in_one_session_passes(tmp_path, monkeypatch):
    rc, r = _run(tmp_path, monkeypatch, "ok")
    assert rc == 0 and r["verdict"] == "PASS", r
    assert r["source_lines_before_landing"] >= 1 and r["landing"]["rc"] == 0 and r["first_translated"]


def test_run_session_that_needs_a_restart_fails(tmp_path, monkeypatch):
    rc, r = _run(tmp_path, monkeypatch, "never", timeout=6)
    assert rc == 1 and any("no translated line after" in p for p in r["problems"])


def test_run_model_not_really_missing_fails(tmp_path, monkeypatch):
    rc, r = _run(tmp_path, monkeypatch, "early", timeout=6)
    assert rc == 1 and any("before Hy-MT2 landed" in p for p in r["problems"])


def test_hold_takes_a_cached_model_away_and_lands_it_back(tmp_path):
    from tinglongwu.hymt import HYMT_SIZES, hymt_path
    from tinglongwu.mt_pending import hymt_complete

    gguf = hymt_path(tmp_path, "1.8b")
    gguf.parent.mkdir(parents=True, exist_ok=True)
    gguf.write_bytes(b"gguf")
    (tmp_path / "manifest.json").write_text(json.dumps({HYMT_SIZES["1.8b"][0]: "sha", "whisper": "x"}), "utf-8")
    assert hymt_complete(tmp_path)
    hold = h.Hold(tmp_path)
    assert not hymt_complete(tmp_path)
    assert "whisper" in json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert hold.land() == 0 and hymt_complete(tmp_path) and gguf.read_bytes() == b"gguf"


def test_run_crash_right_after_a_translated_line_fails(tmp_path, monkeypatch):
    rc, r = _run(tmp_path, monkeypatch, "crash", timeout=10)
    assert rc == 1 and any("exited with code 3" in p for p in r["problems"]), r


def test_run_fall_back_to_source_after_translating_fails(tmp_path, monkeypatch):
    # settle 10 s: the fallback (about 2.7 s after the translated row) always falls inside the window, and the
    # 2.5 s pause spans at least two 1 s polls, so a poll sees the translated row alone first
    rc, r = _run(tmp_path, monkeypatch, "flap", timeout=30, settle=10)
    assert rc == 1 and any("not one-way" in p for p in r["problems"]), r


def test_missing_clip_leaves_a_cached_model_in_place(tmp_path):
    from tinglongwu.hymt import HYMT_SIZES, hymt_path
    from tinglongwu.mt_pending import hymt_complete

    gguf = hymt_path(tmp_path, "1.8b")
    gguf.parent.mkdir(parents=True, exist_ok=True)
    gguf.write_bytes(b"gguf")
    (tmp_path / "manifest.json").write_text(json.dumps({HYMT_SIZES["1.8b"][0]: "sha"}), "utf-8")
    import pytest

    with pytest.raises(FileNotFoundError):
        h.main(["--models-dir", str(tmp_path), "--clip", str(tmp_path / "nope.wav"), "--timeout", "5",
                "--log", str(tmp_path / "h.log"), "--out", str(tmp_path / "h.json")])
    assert hymt_complete(tmp_path)


def test_read_jsonl_skips_a_half_written_row(tmp_path):
    p = tmp_path / "l.jsonl"
    full = json.dumps(TR, ensure_ascii=False).encode() + b"\n"
    p.write_bytes(full + json.dumps(TR, ensure_ascii=False).encode()[:-3])  # cut inside a UTF-8 character
    assert h.read_jsonl(p) == [TR]


def test_run_deadline_inside_the_settle_window_fails(tmp_path, monkeypatch):
    fake = tmp_path / "fake_tlwu.py"
    fake.write_text(FAKE, "utf-8")
    mdir = tmp_path / "models"
    mdir.mkdir()
    monkeypatch.setenv("FAKE_MODE", "ok")
    land = f'"{sys.executable}" -c "import pathlib, sys; pathlib.Path(sys.argv[2], \'landed\').touch()"'
    out = tmp_path / "h.json"
    rc = h.main(["--models-dir", str(mdir), "--land-cmd", land, "--tlwu", f'"{sys.executable}" "{fake}"',
                 "--timeout", "15", "--settle", "120", "--log", str(tmp_path / "h.log"), "--out", str(out)])
    r = json.loads(out.read_text("utf-8"))
    assert rc == 1 and any("did not watch a full" in p for p in r["problems"]), r


def test_run_landing_failure_ends_the_check_early(tmp_path, monkeypatch):
    fake = tmp_path / "fake_tlwu.py"
    fake.write_text(FAKE, "utf-8")
    mdir = tmp_path / "models"
    mdir.mkdir()
    monkeypatch.setenv("FAKE_MODE", "ok")
    out = tmp_path / "h.json"
    rc = h.main(["--models-dir", str(mdir), "--land-cmd", f'"{sys.executable}" -c "raise SystemExit(1)"',
                 "--tlwu", f'"{sys.executable}" "{fake}"', "--timeout", "60", "--log", str(tmp_path / "h.log"),
                 "--out", str(out)])
    r = json.loads(out.read_text("utf-8"))
    assert rc == 1 and r["landing"]["rc"] == 1 and r["elapsed_seconds"] < 20 \
        and any("landing did not finish" in p for p in r["problems"]), r


def test_read_jsonl_keeps_a_row_with_u2028(tmp_path):
    p = tmp_path / "l.jsonl"
    row = {"src": "a\u2028b", "dst": ""}
    p.write_text(json.dumps(row, ensure_ascii=False) + "\n", "utf-8")
    assert h.read_jsonl(p) == [row]


def test_run_clean_exit_inside_the_settle_window_fails(tmp_path, monkeypatch):
    rc, r = _run(tmp_path, monkeypatch, "quit", timeout=15)
    assert rc == 1 and r["exit"] == 0 and any("did not watch a full" in p for p in r["problems"]), r
