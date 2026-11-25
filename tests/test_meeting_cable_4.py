"""#4: the meeting-mode label check (scripts/hw_live_checks.py meeting) on a GitHub-hosted runner with one VB-CABLE
(audio-routing.yml, `meeting --cable`): clip A is the speak side's paced --input, clip B is played into "CABLE Input"
and the listen side captures that cable through WASAPI loopback."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks_cable_4", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()
REF_A = "今天下午我们在会议室讨论新产品的价格"
REF_B = "the quarterly report shows strong growth in every region"


def _proto_args(cmd: list[str]) -> list[str]:
    i = cmd.index("--")
    assert cmd[i - 1] in ("_meeting-child", "--cable")
    return cmd[i + 1:]


def test_cable_command_replays_clip_a_and_listens_to_the_cable(tmp_path):
    from tinglongwu.reverse import proto

    wav = tmp_path / "a.wav"
    sf.write(str(wav), np.zeros(1600, np.float32), 16000)
    cmd = hl.meeting_cmd(wav, "M", tmp_path / "s.jsonl", hl.CABLE_IN, cable=True)
    assert "--cable" in cmd
    a = proto.build_parser().parse_args(_proto_args(cmd))
    assert a.paced and a.input == str(wav) and a.mic is None and a.no_tts and a.no_confirm
    lis = a.listen
    assert lis[lis.index("--capture-device") + 1] == "CABLE Input"
    assert lis[lis.index("--save") + 1] == str(tmp_path / "s.jsonl")
    # owner PC (no --cable): unchanged, the mic is CABLE Output and the listen side the real speakers
    own = proto.build_parser().parse_args(_proto_args(hl.meeting_cmd(wav, "M", tmp_path / "s.jsonl", "Speakers")))
    assert own.mic == "CABLE Output" and not own.paced and "--cable" not in hl.meeting_cmd(wav, "M", tmp_path, "S")
    assert own.listen[own.listen.index("--capture-device") + 1] == "Speakers"


def test_meeting_child_cable_lets_the_listen_side_capture_the_cable(monkeypatch):
    from tinglongwu.capture import wasapi
    from tinglongwu.reverse import live, proto

    got = []
    monkeypatch.setattr(proto, "main", lambda argv: got.append(argv) or 0)
    monkeypatch.setattr(live, "check_mic_feedback", live.check_mic_feedback)
    monkeypatch.setattr(wasapi, "is_own_output", wasapi.is_own_output)
    assert wasapi.is_own_output("CABLE Input (VB-Audio Virtual Cable)")
    assert hl.main(["_meeting-child", "--", "--mic", "X"]) == 0
    assert got[-1] == ["--mic", "X"] and wasapi.is_own_output("CABLE Input (VB-Audio Virtual Cable)")
    assert hl.main(["_meeting-child", "--cable", "--", "--input", "a.wav", "--paced"]) == 0
    assert got[-1] == ["--input", "a.wav", "--paced"]
    assert not wasapi.is_own_output("CABLE Input (VB-Audio Virtual Cable)")


def test_pad_wav_appends_silence(tmp_path):
    x = np.full(16000, 0.25, np.float32)
    sf.write(str(tmp_path / "a.wav"), x, 16000)
    y, sr = sf.read(str(hl.pad_wav(tmp_path / "a.wav", tmp_path / "p.wav", 2.5)), dtype="float32")
    assert sr == 16000 and len(y) == 16000 + 40000
    assert abs(float(y[:16000].mean()) - 0.25) < 1e-3 and not y[16000:].any()


def test_run_meeting_cable_plays_only_clip_b_into_the_cable(tmp_path, monkeypatch):
    sf.write(str(tmp_path / "a.wav"), np.full(1600, 0.1, np.float32), 16000)
    meta = {"meet_a_zh": {"wav": "a.wav", "ref": REF_A}, "meet_b_en": {"wav": "b.wav", "ref": REF_B}}
    (tmp_path / "clips.json").write_text(json.dumps(meta), "utf-8")
    seen = {}

    class FakeChild:
        def __init__(self, cmd, log):
            seen["cmd"] = cmd

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def wait_for(self, *marks, timeout=0):
            seen["marks"] = marks
            return True

        def close(self):
            rows = [{"side": "me", "src": REF_A}, {"side": "them", "src": REF_B}]
            Path(seen["cmd"][seen["cmd"].index("--save") + 1]).write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")

    plays = []
    monkeypatch.setattr(hl, "Child", FakeChild)
    monkeypatch.setattr(hl, "play_child", lambda wav, dev: plays.append((Path(wav).name, dev)) or object())
    monkeypatch.setattr(hl, "played_at", lambda p: 0.0)
    monkeypatch.setattr(hl, "kill_proc", lambda p: None)
    monkeypatch.setattr(hl, "TAIL_S", 0.0)
    monkeypatch.setitem(sys.modules, "soundcard", None)  # no sound card: --cable must not ask for real speakers
    ok, msg = hl.run_meeting(tmp_path, "M", tmp_path, cable=True)
    assert ok, msg
    assert plays == [("b.wav", "CABLE Input")]
    assert hl.PACED_MARK in seen["marks"] and hl.READY_MARK in seen["marks"]
    me = seen["cmd"][seen["cmd"].index("--input") + 1]
    y, sr = sf.read(me, dtype="float32")
    assert len(y) / sr >= hl.MEET_S + hl.READY_TIMEOUT_S  # clip A + silence keeps the process up while B plays
