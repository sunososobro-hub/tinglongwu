"""#1247: `hw_live_checks.py file` runs only the --file half of the #894 check (any machine, no audio devices) and
says, per clip of the joined recording, whether any saved line covers it and what tlwu wrote there."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks_file_1247", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()
REFS = ["the quarterly report shows strong growth in every region",
        "scientists discovered seven species living beneath antarctic glaciers",
        "children painted colourful murals across abandoned warehouse walls"]
SPANS = [[0.0, 10.0], [11.0, 21.0], [22.0, 32.0]]
REC = [{"start": 0.0, "end": 10.4, "source": "The quarterly report shows strong growth in every region."},
       {"start": 21.6, "end": 32.3, "source": "Children painted murals across warehouse walls."}]


def test_clip_lines_maps_saved_line_audio_to_clips():
    per = hl.clip_lines(SPANS, REFS, REC)
    assert [r["lines"] for r in per] == [1, 0, 1]
    assert per[0]["hit"] == 1.0 and per[1]["hit"] == 0.0
    assert per[2]["hit"] >= hl.MIN_CLIP_HIT  # 5 of 7 long words
    assert per[2]["src"] == ["Children painted murals across warehouse walls."]


def test_clip_table_lists_only_missed_clips_and_counts_no_line():
    per = hl.clip_lines(SPANS, REFS, [REC[0], {"start": 11.0, "end": 21.0, "source": "Thank you."}])
    out = hl.clip_table(per).splitlines()
    assert out[0] == "clips missed 2/3 (no line: 1)"
    assert out[1].startswith("  clip 1 11-21 s lines=1 hit=0.00") and "Thank you." in out[1]
    assert out[2].startswith("  clip 2 22-32 s lines=0 hit=0.00")


def test_clip_lines_names_the_clip_whose_words_a_line_holds():
    per = hl.clip_lines(SPANS, REFS, [{"start": 11.0, "end": 21.0, "source": REFS[2]}])
    assert per[1]["best_ref"] == 2 and per[0]["best_ref"] is None
    assert "got-is-clip=2" in hl.clip_table(per)


def test_add_levels_tells_a_silent_clip_from_speech():
    import numpy as np

    audio = np.zeros(32 * hl.SR, np.float32)
    audio[: 10 * hl.SR] = 0.1
    per = hl.clip_lines(SPANS, REFS, [])
    hl.add_levels(per, audio)
    assert per[0]["rms_dbfs"] == -20.0 and per[1]["rms_dbfs"] == -120.0
    assert "rms=-120.0dBFS" in hl.clip_table(per)


def _wav(tmp_path):
    import numpy as np
    import soundfile as sf

    sf.write(str(tmp_path / "mic_en.wav"), np.zeros(33 * hl.SR, np.float32), hl.SR)


def test_file_subcommand_runs_tlwu_on_the_mic_clip_and_reports(tmp_path, monkeypatch, capsys):
    (tmp_path / "clips.json").write_text(json.dumps({"mic_en": {
        "wav": "mic_en.wav", "speech": [0.0, 32.0], "ref": " ".join(REFS), "spans": SPANS, "refs": REFS}}), "utf-8")
    seen = {}
    _wav(tmp_path)

    def fake_run(cmd, check, stdout):
        seen["cmd"] = cmd
        save = Path(cmd[cmd.index("--save") + 1])
        rec = Path(cmd[cmd.index("--record") + 1])
        rec.mkdir(parents=True)
        save.write_text("".join(json.dumps({"src": r["source"]}) + "\n" for r in REC), "utf-8")
        (rec / "segments.jsonl").write_text("".join(json.dumps(r) + "\n" for r in REC), "utf-8")

    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    rc = hl.main(["file", "--clips", str(tmp_path), "--models-dir", "M", "--", "--device", "cpu"])
    out = capsys.readouterr().out
    assert rc == 0
    cmd = seen["cmd"]
    assert cmd[cmd.index("--file") + 1] == str(tmp_path / "mic_en.wav")
    assert cmd[-2:] == ["--device", "cpu"] and "--transcript-only" in cmd
    assert "clips missed 1/3 (no line: 1)" in out
    assert "PASS #1247 file baseline of the #894 clip: WER" in out and "clips missed 1/3" in out
    rep = json.loads((tmp_path / "file_report.json").read_text("utf-8"))
    assert [c["lines"] for c in rep["clips"]] == [1, 0, 1]


def test_file_subcommand_fails_when_tlwu_saved_nothing(tmp_path, monkeypatch, capsys):
    (tmp_path / "clips.json").write_text(json.dumps({"mic_en": {
        "wav": "mic_en.wav", "speech": [0.0, 32.0], "ref": " ".join(REFS), "spans": SPANS, "refs": REFS}}), "utf-8")
    monkeypatch.setattr(hl.subprocess, "run", lambda cmd, check, stdout: None)
    assert hl.main(["file", "--clips", str(tmp_path), "--models-dir", "M"]) == 1
    assert "FAIL #1247 file baseline" in capsys.readouterr().out


def test_file_subcommand_fails_when_the_recording_is_missing(tmp_path, monkeypatch, capsys):
    (tmp_path / "clips.json").write_text(json.dumps({"mic_en": {
        "wav": "mic_en.wav", "speech": [0.0, 32.0], "ref": " ".join(REFS), "spans": SPANS, "refs": REFS}}), "utf-8")

    def fake_run(cmd, check, stdout):  # lines saved, but --record wrote nothing
        Path(cmd[cmd.index("--save") + 1]).write_text(json.dumps({"src": REC[0]["source"]}) + "\n", "utf-8")

    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    assert hl.main(["file", "--clips", str(tmp_path), "--models-dir", "M"]) == 1
    out = capsys.readouterr().out
    assert "FAIL #1247 file baseline" in out and "no --record output" in out and "clips missed" not in out


def test_a_repeated_reference_does_not_name_the_clip_its_twin():
    refs = REFS + [REFS[0]]  # the clip list cycles: clip 3 has clip 0's text
    spans = SPANS + [[33.0, 43.0]]
    per = hl.clip_lines(spans, refs, [REC[0], {"start": 33.0, "end": 43.0, "source": REFS[0]}])
    assert per[0]["best_ref"] is None and per[3]["best_ref"] is None


def test_clip_table_compares_loudness_of_heard_and_missed_clips():
    import numpy as np

    audio = np.zeros(33 * hl.SR, np.float32)
    audio[: 11 * hl.SR] = 0.1  # clip 0 heard and loud; clips 1, 2 missed and silent
    per = hl.clip_lines(SPANS, REFS, [REC[0]])
    hl.add_levels(per, audio)
    head = hl.clip_table(per).splitlines()[0]
    assert "rms dBFS median heard -20.0 (min -20.0), missed -120.0 (max -120.0)" in head


def test_a_failed_run_removes_the_previous_report(tmp_path, monkeypatch):
    (tmp_path / "clips.json").write_text(json.dumps({"mic_en": {
        "wav": "mic_en.wav", "speech": [0.0, 32.0], "ref": " ".join(REFS), "spans": SPANS, "refs": REFS}}), "utf-8")
    (tmp_path / "file_report.json").write_text("{}", "utf-8")
    monkeypatch.setattr(hl.subprocess, "run", lambda cmd, check, stdout: None)
    assert hl.main(["file", "--clips", str(tmp_path), "--models-dir", "M"]) == 1
    assert not (tmp_path / "file_report.json").exists()
