"""#1194: owner-PC hw-tests live checks (scripts/hw_live_checks.py) for #894 / #4 / #348; the scoring and clip
building run here on Linux, the device parts only on the self-hosted runner."""
from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()
REF_A = "今天下午我们在会议室讨论新产品的价格"
REF_B = "the quarterly report shows strong growth in every region"


def test_layout_cycles_clips_until_the_target_and_keeps_the_gap():
    lay = hl.layout([10.0, 5.0], 40.0, gap_s=1.0)
    assert [k for k, _s, _e in lay] == [0, 1, 0, 1, 0]
    assert lay[1] == (1, 11.0, 16.0) and lay[-1][2] >= 40.0
    assert hl.layout([10.0, 5.0, 8.0], 1.0, start=1)[0][0] == 1


def test_max_gap_counts_the_start_the_end_and_ignores_lines_outside_the_speech():
    assert hl.max_gap([5.0, 12.0, 30.0], 0.0, 35.0) == pytest.approx(18.0)
    assert hl.max_gap([], 2.0, 9.0) == pytest.approx(7.0)
    assert hl.max_gap([-3.0, 4.0, 99.0], 0.0, 8.0) == pytest.approx(4.0)


def test_wall_offsets_from_saved_lines():
    t0 = datetime.datetime(2026, 11, 16, 12, 0, 0).astimezone().timestamp()
    rows = [{"wall": datetime.datetime.fromtimestamp(t0 + 7).astimezone().isoformat(timespec="seconds"), "src": "x"},
            {"src": "no wall"}]
    assert hl.wall_offsets(rows, t0) == [pytest.approx(7.0)]


def test_check_mic_passes_within_1_25x_and_10_s():
    ok, msg = hl.check_mic(0.10, 0.12, 8.0, 40)
    assert ok and msg.startswith("WER mic 0.120 vs file 0.100 (x1.20")


@pytest.mark.parametrize("file_wer,mic_wer,gap,n,why", [
    (0.10, 0.13, 3.0, 40, "mic WER > 1.25x file WER"),
    (0.10, 1.0, 300.0, 0, "no line from the microphone"),
])
def test_check_mic_fails(file_wer, mic_wer, gap, n, why):
    ok, msg = hl.check_mic(file_wer, mic_wer, gap, n)
    assert not ok and why in msg


def test_check_mic_gap_is_report_only():
    # #1247: a long subtitle gap is printed but no longer fails the check
    ok, msg = hl.check_mic(0.10, 0.10, 10.5, 40)
    assert ok and "longest gap 10.5 s (report only" in msg


def test_origin_by_reference_tokens():
    assert hl.origin("我们在会议室讨论价格", REF_A, REF_B) == "a"
    assert hl.origin("The quarterly report shows growth.", REF_A, REF_B) == "b"
    assert hl.origin("completely unrelated words here", REF_A, REF_B) is None


def test_check_meeting_passes_when_them_is_only_the_system_audio():
    rows = [{"side": "me", "src": "我们在会议室讨论"}] * 5 + [{"side": "them", "src": "strong growth in every region"}] * 5
    ok, msg = hl.check_meeting(rows, REF_A, REF_B)
    assert ok and msg.startswith("10/10 lines labelled right (100%")


def test_check_meeting_fails_on_swapped_labels_and_a_missing_side():
    rows = [{"side": "them", "src": "我们在会议室讨论"}] + [{"side": "them", "src": "strong growth in every region"}] * 8
    ok, msg = hl.check_meeting(rows, REF_A, REF_B)
    assert not ok and "8/9" in msg and "< 90%" in msg
    ok, msg = hl.check_meeting([{"side": "them", "src": "the quarterly report"}], REF_A, REF_B)
    assert not ok and "no line from clip A (mic)" in msg
    assert not hl.check_meeting([], REF_A, REF_B)[0]


def test_check_loopback_needs_a_line_from_the_played_speech():
    assert hl.check_loopback([{"src": "the quarterly report shows"}], REF_B)[0]
    ok, msg = hl.check_loopback([{"src": "thank you for watching"}], REF_B)
    assert not ok and "nothing from the other process" in msg
    assert not hl.check_loopback([], REF_B)[0]


def test_read_jsonl_skips_non_line_rows(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"t": 1, "side": "them", "src": "a"}\n{"fix": 1}\n\n', "utf-8")
    assert hl.read_jsonl(p) == [{"t": 1, "side": "them", "src": "a"}]
    assert hl.read_jsonl(tmp_path / "missing.jsonl") == []


def _fake_fleurs(d: Path) -> None:
    rows = []
    for lang, n in (("en", 20), ("zh", 20)):
        for i in range(n):
            cid = f"{lang}_{i:03d}"
            sf.write(str(d / f"{cid}.wav"), np.full(int(9.0 * hl.SR), 0.1, np.float32), hl.SR)
            rows.append({"id": cid, "lang": lang, "wav": f"{cid}.wav", "ref_text": f"{lang} text {i}"})
    (d / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")


def test_clips_subcommand_builds_the_three_clips(tmp_path):
    data, out = tmp_path / "data", tmp_path / "clips"
    data.mkdir()
    _fake_fleurs(data)
    r = subprocess.run([sys.executable, str(SCRIPT), "clips", "--data", str(data), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    meta = json.loads((out / "clips.json").read_text("utf-8"))
    mic = meta["mic_en"]
    assert mic["speech"][1] >= hl.MIC_S and len(set(mic["clips"])) == 20 and len(mic["clips"]) == 31
    x, sr = sf.read(str(out / "mic_en.wav"))
    assert sr == hl.SR and len(x) / sr == pytest.approx(mic["seconds"]) and abs(x[int(9.5 * sr)]) == 0  # the gap
    assert meta["meet_a_zh"]["lang"] == "zh" and meta["meet_b_en"]["clips"][0] == "en_010"
    assert meta["meet_a_zh"]["ref"].startswith("zh text 0 zh text 1") and meta["loop_en"]["speech"][1] >= hl.LOOP_S


def test_verify_offline_stop_file_ends_a_mic_watch(tmp_path):
    stop = tmp_path / "stop"
    stop.write_text("x")
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_offline.py"), "--mic", "--seconds", "600",
                        "--stop-file", str(stop)], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout + r.stderr
    assert time.monotonic() - t0 < 60


# ---- #1203: no stray processes, no lost PASS line ----

def _child(code: str, tmp_path, name="c.log"):
    return hl.Child([sys.executable, "-c", code], tmp_path / name)


def test_child_close_terminates_a_child_that_never_gets_ready(tmp_path):
    ch = _child("import time; time.sleep(600)", tmp_path)
    assert ch.wait_for("never printed", timeout=0.5) is False
    assert ch.p.poll() is None
    ch.close()
    assert ch.p.poll() is not None and not ch.reader.is_alive() and ch.log.closed


def test_child_context_manager_cleans_up_on_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with _child("import time; time.sleep(600)", tmp_path) as ch:
            raise RuntimeError("boom")
    assert ch.p.poll() is not None


def test_finish_sees_a_pass_line_printed_right_before_exit(tmp_path):
    for i in range(100):
        ch = _child("print('PASS')", tmp_path, f"c{i}.log")
        ch.finish(30)
        assert any(ln.startswith("PASS") for ln in ch.lines), i
        ch.close()


def test_played_at_kills_the_player_on_timeout(tmp_path, monkeypatch):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], stdout=subprocess.PIPE, text=True)
    monkeypatch.setattr(hl, "MIC_S", 0.1)
    with pytest.raises(subprocess.TimeoutExpired):
        hl.played_at(p)
    assert p.poll() is not None



def test_kill_tree_kills_the_grandchild_too(tmp_path):
    code = ("import subprocess,sys,time;"
            "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(600)']);"
            "print(c.pid,flush=True);time.sleep(600)")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    gpid = int(p.stdout.readline())
    hl.kill_tree(p)
    psutil = pytest.importorskip("psutil")
    time.sleep(0.5)
    assert not psutil.pid_exists(gpid) or psutil.Process(gpid).status() == psutil.STATUS_ZOMBIE
    p.stdout.close()
