"""#1264: real-time meeting replay (scripts/bench/meeting_replay.py) and the live ASR budget for a --paced file.

The metric parts run on synthetic outputs (no models); the replay itself runs in bench-cpu-ab on a 4-vCPU runner."""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tinglongwu import cli

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench" / "meeting_replay.py"


@pytest.fixture(scope="module")
def mr():
    spec = importlib.util.spec_from_file_location("meeting_replay", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["meeting_replay"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- the product change: --paced is live
def test_paced_file_gets_the_live_asr_budget():
    # before #1264 a --paced file decoded without the live time budget, so a replay could never drop a sentence
    assert cli.asr_live("cpu", "talk.wav", True) is True


def test_unpaced_file_and_gpu_keep_full_decoding():
    assert cli.asr_live("cpu", "talk.wav", False) is False
    assert cli.asr_live("cuda", None, False) is False
    assert cli.asr_live("cuda", "talk.wav", True) is False
    assert cli.asr_live("cpu", None, False) is True  # live capture on the CPU (unchanged)


def test_paced_live_recognizer_drops_over_budget(monkeypatch):
    """With live on (what --paced now sets), a decode that yields nothing before its budget is dropped."""
    from tinglongwu.asr import FasterWhisperRecognizer

    r = FasterWhisperRecognizer.__new__(FasterWhisperRecognizer)
    r.lang, r._last, r.glossary = "ru", "", None
    r.live = cli.asr_live("cpu", "x.wav", True)
    clock = iter([0.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    r.clock = lambda: next(clock)

    class _Seg:
        no_speech_prob, avg_logprob, text, end = 0.9, -2.0, "", 1.0  # filtered: nothing kept

    class _Model:
        def transcribe(self, audio, **kw):
            return iter([_Seg()]), type("I", (), {"language": "ru", "language_probability": 1.0})()

    r.model = _Model()
    monkeypatch.setattr(r, "_lang_for", lambda audio: "ru", raising=False)
    assert r.transcribe(np.zeros(16000, np.float32)) is None
    assert r.dropped == 1


# ---------------------------------------------------------------- metrics
def test_layout_short_joins_merge_and_every_fourth_is_a_pause(mr):
    lay = mr.layout([2.0, 3.0], 20.0)
    assert lay[0] == (0, 0.0, 2.0)
    gaps = [round(b[1] - a[2], 2) for a, b in zip(lay, lay[1:])]
    assert gaps[:4] == [mr.SHORT_GAP_S, mr.SHORT_GAP_S, mr.SHORT_GAP_S, mr.LONG_GAP_S]
    assert mr.SHORT_GAP_S < 0.6 < mr.LONG_GAP_S  # Segmenter min_silence 0.6 s
    assert lay[-1][2] >= 20.0 and [k for k, _s, _e in lay[:3]] == [0, 1, 0]


def test_coverage_counts_speech_a_line_overlaps(mr):
    speech = [(0, 10), (12, 22)]
    assert mr.coverage(speech, [(0, 10), (12, 22)]) == pytest.approx(1.0)
    assert mr.coverage(speech, [(0, 10)]) == pytest.approx(0.5)  # the second sentence was dropped
    assert mr.coverage(speech, [(5, 15), (14, 17)]) == pytest.approx(10 / 20)  # overlap counted once
    assert mr.coverage(speech, []) == 0.0


def test_longest_gap_and_p95(mr):
    assert mr.longest_gap([5, 50, 52], 0, 60) == (45, 5, 50)
    assert mr.longest_gap([], 10, 40) == (30, 10, 40)
    assert mr.p95([]) is None
    assert mr.p95(list(range(1, 101))) == 95
    assert mr.p95([3.0]) == 3.0


def test_count_events_and_labels(mr):
    log = ["tlwu: asr: dropped a sentence over its time budget (13.0 s > 12.0 s; 1 dropped so far)",
           "tlwu: asr: cut a sentence at its time budget (9.1 s > 9.0 s; kept 2.0 of 3.0 s; 1 cut so far)",
           "tlwu: asr: dropped a sentence over its time budget (20.0 s > 12.0 s; 2 dropped so far)", "hello"]
    assert mr.count_events(log) == (2, 1)
    assert mr.label_count([{"speaker": "Speaker 1"}, {"speaker": "Speaker 2"}, {"speaker": "Speaker 1"}, {}]) == 2


def test_tlwu_cmd_is_a_paced_live_replay(mr, tmp_path):
    meta = {"wav": "replay.wav", "lang": "ru"}
    cmd = mr.tlwu_cmd(tmp_path, meta, "/m", True, ["--engine", "hymt"])
    for flag in ("--paced", "--save", "--latency-jsonl", "--record", "--speakers"):
        assert flag in cmd
    assert cmd[cmd.index("--lang") + 1] == "ru" and cmd[cmd.index("--models-dir") + 1] == "/m"
    assert cmd[-2:] == ["--engine", "hymt"]
    assert mr.tlwu_cmd(tmp_path, meta, None, False, ["--lang", "auto"]).count("--lang") == 1


def test_cpu_set(mr):
    assert mr.cpu_set(4, [0, 1, 2, 3, 4, 5, 6, 7]) == [0, 1, 2, 3]
    assert mr.cpu_set(4, [0, 1]) == [0, 1]
    assert mr.cpu_set(0, [3, 1]) == [1, 3]


def _iso(t0: float, s: float) -> str:
    return datetime.datetime.fromtimestamp(t0 + s).astimezone().isoformat(timespec="seconds")


def test_build_then_report_from_run_outputs(mr, tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    rows = []
    for i in range(3):
        sf.write(str(data / f"ru_{i}.wav"), np.full(16000 * 4, 0.1, np.float32), 16000)
        rows.append({"id": f"ru-{i}", "lang": "ru", "wav": f"ru_{i}.wav", "ref_text": "x"})
    rows.append({"id": "en-0", "lang": "en", "wav": "ru_0.wav", "ref_text": "x"})
    (data / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows), "utf-8")
    rdir = tmp_path / "replay"
    meta = mr.build(data, "ru", 0.5, rdir)
    audio, rate = sf.read(str(rdir / "replay.wav"))
    assert rate == 16000 and len(audio) / rate == pytest.approx(meta["seconds"], abs=0.01)
    assert meta["spans"][0] == [0.0, 4.0] and meta["speech"][1] >= 30
    assert set(meta["clips"]) == {"ru-0", "ru-1", "ru-2"}

    # a run: lines for every span but the third (dropped), one long stretch with no line, two speakers
    t0 = 1_800_000_000.0
    spans = meta["spans"]
    segs = [{"start": s, "end": e} for i, (s, e) in enumerate(spans) if i != 2]
    saved = [{"wall": _iso(t0, e + 1), "speaker": f"Speaker {i % 2 + 1}"} for i, (s, e) in enumerate(spans) if i != 2]
    (rdir / "rec").mkdir()
    (rdir / "rec" / "segments.jsonl").write_text("\n".join(json.dumps(s) for s in segs), "utf-8")
    (rdir / "saved.jsonl").write_text("\n".join(json.dumps(s) for s in saved), "utf-8")
    (rdir / "latency.jsonl").write_text("\n".join(json.dumps({"total": x}) for x in (1.0, 2.0, 9.0)), "utf-8")
    (rdir / "tlwu.log").write_text("tlwu: CPU mode threads: 4 CPUs -> asr=2 mt=2/2\n"
                                   "tlwu: asr: dropped a sentence over its time budget (x)\n", "utf-8")
    (rdir / "t0.txt").write_text(f"{t0}\n", "utf-8")
    m = mr.report(rdir)
    speech = sum(e - s for s, e in spans)
    assert m["drops"] == 1 and m["cuts"] == 0
    assert m["coverage_pct"] == pytest.approx(round(100 * (speech - 4.0) / speech, 1))
    assert m["lag_p95_s"] == 9.0 and m["labels"] == 2 and m["clips_distinct"] == 3
    # the gap spans the dropped clip: from the line after clip 1 to the line after clip 3
    assert m["longest_gap_s"] == pytest.approx(spans[3][1] - spans[1][1], abs=1.01)
    out = capsys.readouterr().out
    assert "| drops | 1 (cuts 0) |" in out and "asr=2 mt=2/2" in out
    assert out.strip().splitlines()[-1].startswith("replay: drops=1 ")
    assert json.loads((rdir / "metrics.json").read_text("utf-8"))["drops"] == 1


def test_report_without_a_run_prints_na(mr, tmp_path):
    (tmp_path / "replay.json").write_text(json.dumps({"wav": "r.wav", "lang": "ru", "seconds": 10.0,
                                                      "speech": [0, 8], "spans": [[0, 8]], "clips": ["a"]}), "utf-8")
    m = mr.report(tmp_path)
    assert m["drops"] == 0 and m["coverage_pct"] == 0.0 and m["longest_gap_s"] is None and m["lag_p95_s"] is None


def _fake_tlwu(tmp_path, code: str, exit_code: int) -> Path:
    """A stand-in for `python -m tinglongwu`: prints the ready line, runs `code`, exits with `exit_code`."""
    pkg = tmp_path / "fake" / "tinglongwu"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", "utf-8")
    (pkg / "__main__.py").write_text(
        "import sys\nprint('* ready - listening (start playback now)', flush=True)\n" + code
        + f"\nsys.exit({exit_code})\n", "utf-8")
    return tmp_path / "fake"


def _replay_dir(tmp_path) -> Path:
    rdir = tmp_path / "r"
    (rdir / "rec").mkdir(parents=True)
    (rdir / "replay.json").write_text(json.dumps({"wav": "r.wav", "lang": "ru", "seconds": 0.1, "speech": [0, 0.1],
                                                  "spans": [[0, 0.1]], "clips": ["a"]}), "utf-8")
    return rdir


def test_run_fails_when_tlwu_crashes_mid_replay(mr, tmp_path, monkeypatch):
    fake = _fake_tlwu(tmp_path, "print('Traceback: boom', flush=True)", 3)
    monkeypatch.setenv("PYTHONPATH", str(fake))
    monkeypatch.setattr(mr, "ROOT", tmp_path)
    rdir = _replay_dir(tmp_path)
    with pytest.raises(SystemExit, match="exited with 3"):
        mr.run(rdir, None, 0, False, [], drain=5)
    assert (rdir / "metrics.json").is_file()  # partial metrics are still written


def test_run_starts_from_a_clean_recording_manifest(mr, tmp_path, monkeypatch):
    fake = _fake_tlwu(tmp_path, "", 0)
    monkeypatch.setenv("PYTHONPATH", str(fake))
    monkeypatch.setattr(mr, "ROOT", tmp_path)
    rdir = _replay_dir(tmp_path)
    (rdir / "rec" / "segments.jsonl").write_text(json.dumps({"start": 0, "end": 0.1}) + "\n", "utf-8")
    m = mr.run(rdir, None, 0, False, [], drain=5)  # a clean exit 0 succeeds
    assert m["coverage_pct"] == 0.0  # the old run's row is not counted
