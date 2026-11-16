"""#1253: meeting replay A/B (ASR only vs ASR + MT) and the MT delay the replay reports.

Runs on synthetic run outputs (no models); the replays themselves run in bench-cpu-ab (job `replay`, two variants,
then `replay-ab`) on a 4-vCPU runner."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bench" / "meeting_replay.py"


@pytest.fixture(scope="module")
def mr():
    spec = importlib.util.spec_from_file_location("meeting_replay_ab", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["meeting_replay_ab"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_dir(root: Path, name: str, drops: int, cuts: int, lat: list[dict], seconds: float = 20.0,
             clips: tuple = ("a", "b"), ran: bool = True) -> Path:
    d = root / name
    (d / "rec").mkdir(parents=True)
    if ran:
        (d / "t0.txt").write_text("1800000000.0\n", "utf-8")
        (d / "done.json").write_text(json.dumps({"completed": True, "exit": 0}), "utf-8")
    (d / "replay.json").write_text(json.dumps({"wav": "r.wav", "lang": "ru", "seconds": seconds, "speech": [0, 16],
                                               "spans": [[0, 8], [8.25, 16]], "clips": list(clips),
                                               "voiced": [[0.5, 7.5], [8.5, 15.5]]}), "utf-8")
    (d / "rec" / "segments.jsonl").write_text(json.dumps({"start": 0.0, "end": 16.0}) + "\n", "utf-8")
    log = ["tlwu: asr: dropped a sentence over its time budget (x)"] * drops
    log += ["tlwu: asr: cut a sentence at the time budget (y)"] * cuts
    (d / "tlwu.log").write_text("\n".join(log) + "\n", "utf-8")
    (d / "latency.jsonl").write_text("\n".join(json.dumps(r) for r in lat) + "\n", "utf-8")
    return d


def test_mt_delay_counts_lines_held_for_asr(mr):
    lat = [{"asr": 1.0, "mt": 0.5, "mt_hold": 0.0}, {"asr": 3.0, "mt": 4.0, "mt_hold": 3.2},
           {"asr": 2.0, "mt": 1.0, "mt_hold": 0.4}, {"asr": 9.0, "mt": 2.0, "mt_hold": 0.0}]
    m = mr.mt_delay(lat)
    assert m["mt_held_lines"] == 2 and m["mt_hold_max_s"] == 3.2 and m["mt_hold_p95_s"] == 3.2
    assert m["mt_p50_s"] == 1.0 and m["mt_p95_s"] == 4.0 and m["asr_p95_s"] == 9.0


def test_mt_delay_asr_only_run_is_na_not_zero(mr):
    # --transcript-only: lines carry no "mt_hold" -> n/a; a run where MT never waited -> 0 lines, max 0
    assert mr.mt_delay([{"asr": 1.0, "mt": 0.0}])["mt_held_lines"] is None
    m = mr.mt_delay([{"asr": 1.0, "mt": 0.3, "mt_hold": 0.0}])
    assert m["mt_held_lines"] == 0 and m["mt_hold_max_s"] == 0.0 and m["mt_hold_p95_s"] is None
    assert mr.mt_delay([])["mt_p50_s"] is None


def test_report_shows_the_mt_delay(mr, tmp_path, capsys):
    d = _run_dir(tmp_path, "b", 0, 0, [{"asr": 1.0, "mt": 2.0, "mt_hold": 1.5, "total": 3.5}])
    m = mr.report(d)
    assert m["mt_held_lines"] == 1 and m["mt_hold_max_s"] == 1.5
    out = capsys.readouterr().out
    assert "| MT held for ASR | 1 line(s), p95 1.5 s, max 1.5 s |" in out
    assert "| MT delay p50 / p95 | 2.0 s / 2.0 s |" in out


def test_compare_cli_prints_the_drop_delta(mr, tmp_path, capsys):
    a = _run_dir(tmp_path, "asr", 1, 0, [{"asr": 1.0, "mt": 0.0, "total": 1.2}])
    b = _run_dir(tmp_path, "mt", 3, 1, [{"asr": 2.0, "mt": 1.0, "mt_hold": 0.8, "total": 3.0}])
    out_json = tmp_path / "ab.json"
    assert mr.main(["compare", "--asr-only", str(a), "--with-mt", str(b), "--out", str(out_json)]) == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "replay-ab: drops asr_only=1 with_mt=3 delta=2 cuts_delta=1"
    assert "| drops | 1 | 3 | +2 |" in out and "| MT held lines | n/a | 1 | n/a |" in out
    assert "did not replay the same recording" not in out
    c = json.loads(out_json.read_text("utf-8"))
    assert c["drops_delta"] == 2 and c["same_recording"] is True


def test_compare_equal_drops_and_different_recordings(mr, tmp_path, capsys):
    a = _run_dir(tmp_path, "asr", 2, 0, [], seconds=20.0)
    b = _run_dir(tmp_path, "mt", 2, 0, [], seconds=25.0)
    c = mr.compare_dirs(a, b)
    assert c["drops_delta"] == 0 and c["same_recording"] is False
    assert "**warning: A and B did not replay the same recording**" in capsys.readouterr().out


def test_asr_only_replay_command_passes_transcript_only(mr, tmp_path):
    meta = {"wav": "replay.wav", "lang": "ru"}
    cmd = mr.tlwu_cmd(tmp_path, meta, None, True, ["--transcript-only", "--model", "small"])
    assert cmd[-3:] == ["--transcript-only", "--model", "small"] and "--paced" in cmd and "--speakers" in cmd


def test_non_finite_latency_samples_are_left_out(mr, tmp_path):
    lat = [{"mt": float("inf"), "mt_hold": float("nan"), "asr": float("-inf")}, {"mt": 1.0, "mt_hold": 0.5, "asr": 2.0}]
    m = mr.mt_delay(lat)
    assert m["mt_p95_s"] == 1.0 and m["mt_hold_max_s"] == 0.5 and m["asr_p95_s"] == 2.0
    d = tmp_path / "x"
    d.mkdir()
    (d / "latency.jsonl").write_text("\n".join(json.dumps(r) for r in lat), "utf-8")  # json writes Infinity / NaN
    rows = mr.read_jsonl(d / "latency.jsonl")
    out = json.dumps(mr.mt_delay(rows), allow_nan=False)  # raises on a non-finite number
    assert "Infinity" not in out and "NaN" not in out


def test_same_length_but_other_clips_is_not_the_same_recording(mr, tmp_path, capsys):
    a = _run_dir(tmp_path, "asr", 0, 0, [], clips=("a", "b"))
    b = _run_dir(tmp_path, "mt", 0, 0, [], clips=("c", "d"))
    c = mr.compare_dirs(a, b)
    assert c["same_duration"] is True and c["same_recording"] is False
    assert "did not replay the same recording" in capsys.readouterr().out


def test_a_replay_that_never_ran_gives_no_delta(mr, tmp_path, capsys):
    a = _run_dir(tmp_path, "asr", 0, 0, [], ran=False)
    b = _run_dir(tmp_path, "mt", 0, 0, [])
    c = mr.compare_dirs(a, b)
    out = capsys.readouterr().out
    assert c["both_ran"] is False and c["drops_delta"] is None
    assert "a replay did not run to the end" in out and out.strip().splitlines()[-1].endswith("delta=n/a cuts_delta=n/a")


def test_ready_then_crashed_replay_gives_no_delta(mr, tmp_path, capsys):
    a = _run_dir(tmp_path, "asr", 0, 0, [])
    (a / "done.json").write_text(json.dumps({"completed": False, "exit": 3}), "utf-8")  # ready, then tlwu died
    b = _run_dir(tmp_path, "mt", 0, 0, [])
    c = mr.compare_dirs(a, b)
    assert c["drops_delta"] is None and c["both_ran"] is False
    assert "did not run to the end" in capsys.readouterr().out


def test_run_records_an_early_exit_as_not_completed(mr, tmp_path, monkeypatch):
    pkg = tmp_path / "fake" / "tinglongwu"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", "utf-8")
    (pkg / "__main__.py").write_text("import sys\nprint('* ready - listening (start playback now)', flush=True)\n"
                                     "sys.exit(3)\n", "utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fake"))
    monkeypatch.setattr(mr, "ROOT", tmp_path)
    rdir = tmp_path / "r"
    (rdir / "rec").mkdir(parents=True)
    (rdir / "replay.json").write_text(json.dumps({"wav": "r.wav", "lang": "ru", "seconds": 0.1, "speech": [0, 0.1],
                                                  "spans": [[0, 0.1]], "clips": ["a"]}), "utf-8")
    with pytest.raises(SystemExit, match="exited with 3"):
        mr.run(rdir, None, 0, False, [], drain=5)
    assert json.loads((rdir / "done.json").read_text("utf-8"))["completed"] is False
    assert mr.report(rdir, quiet=True)["completed"] is False


def test_rebuilding_a_directory_drops_the_old_run(mr, tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    data = tmp_path / "data"
    data.mkdir()
    rows = []
    for i, lang in enumerate(("ru", "ru", "en")):
        sf.write(str(data / f"c{i}.wav"), np.full(16000 * 2, 0.1, np.float32), 16000)
        rows.append({"id": f"{lang}-{i}", "lang": lang, "wav": f"c{i}.wav", "ref_text": "x"})
    (data / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows), "utf-8")
    rdir = tmp_path / "replay"
    mr.build(data, "ru", 0.1, rdir)  # recording X, then a run of it
    (rdir / "rec").mkdir()
    (rdir / "t0.txt").write_text("1800000000.0\n", "utf-8")
    (rdir / "done.json").write_text(json.dumps({"completed": True, "exit": 0}), "utf-8")
    (rdir / "rec" / "segments.jsonl").write_text(json.dumps({"start": 0, "end": 2}), "utf-8")
    mr.build(data, "en", 0.1, rdir)  # rebuilt as Y, not run
    m = mr.report(rdir, quiet=True)
    assert m["ran"] is False and m["completed"] is False and not (rdir / "rec" / "segments.jsonl").exists()
