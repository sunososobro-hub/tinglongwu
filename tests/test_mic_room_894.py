"""#894 cloud room-audio check (mic-room.yml): `hw_live_checks.py mic` takes the play / capture devices, so the same
live-mic vs file check runs on a GitHub-hosted ubuntu runner (PulseAudio null sink + a source remapped from its
monitor) and on the owner PC (VB-CABLE, the defaults). The PulseAudio test plays a tone through the real player
subprocess and reads it back through tlwu's MicSource; it runs where `pactl` reaches a server with the mic-room.yml
devices (TLWU_REQUIRE_PULSE=1 there turns a missing setup into a failure)."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_live_checks.py"
ROOM_OUT, ROOM_MIC = "tlwu-Room", "tlwu-Mic"  # device.description of mic-room.yml's null sink / remapped source


def _load():
    spec = importlib.util.spec_from_file_location("hw_live_checks", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = _load()


def _calls(monkeypatch, argv):
    seen = []

    def fake_run_mic(clips, mdir, out, *args):
        seen.append(args)
        return True, "ok"

    monkeypatch.setattr(hl, "run_mic", fake_run_mic)
    rc = hl.main(argv)
    return rc, seen


def test_mic_defaults_to_the_owner_pc_cable(monkeypatch, tmp_path):
    rc, seen = _calls(monkeypatch, ["mic", "--clips", str(tmp_path), "--models-dir", "m"])
    assert rc == 0
    assert seen == [(None, "CABLE Input", "CABLE Output")]


def test_mic_passes_the_cloud_devices_through(monkeypatch, tmp_path):
    rc, seen = _calls(monkeypatch, ["mic", "--clips", str(tmp_path), "--models-dir", "m", "--gap-history", "h.jsonl",
                                    "--play-device", ROOM_OUT, "--capture-device", ROOM_MIC])
    assert rc == 0
    assert seen == [("h.jsonl", ROOM_OUT, ROOM_MIC)]


def test_run_mic_plays_and_listens_on_the_given_devices(monkeypatch, tmp_path):
    """run_mic hands play_device to the player and capture_device to `tlwu --mic --capture-device`."""
    import json

    sr = hl.SR
    sf.write(str(tmp_path / "mic_en.wav"), np.zeros(sr * 2, np.float32), sr)
    (tmp_path / "clips.json").write_text(json.dumps({"mic_en": {
        "wav": "mic_en.wav", "lang": "en", "seconds": 2.0, "speech": [0.0, 2.0], "ref": "hello world",
        "clips": ["en_001"], "spans": [[0.0, 2.0]], "refs": ["hello world"]}}), "utf-8")
    seen = {}

    def fake_run(cmd, **kw):  # the --file half
        save = Path(cmd[cmd.index("--save") + 1])
        save.write_text(json.dumps({"src": "hello world"}) + "\n", "utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    class FakeChild:
        def __init__(self, cmd, log):
            seen["mic_cmd"] = cmd
            self.lines = ["PASS offline"]
            Path(cmd[cmd.index("--save") + 1]).write_text(json.dumps({"src": "hello world", "t": 0.5}) + "\n", "utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def wait_for(self, *marks, timeout=0):
            return True

        def finish(self, timeout=60):
            pass

    def fake_play_child(wav, device):
        seen["play_device"] = device
        return "player"

    monkeypatch.setattr(hl.subprocess, "run", fake_run)
    monkeypatch.setattr(hl, "Child", FakeChild)
    monkeypatch.setattr(hl, "play_child", fake_play_child)
    monkeypatch.setattr(hl, "played_at", lambda p: time.time())
    monkeypatch.setattr(hl, "TAIL_S", 0.0)
    monkeypatch.setattr(hl, "gpu_apps", lambda: [])
    ok, msg = hl.run_mic(tmp_path, "m", tmp_path, None, ROOM_OUT, ROOM_MIC)
    assert seen["play_device"] == ROOM_OUT
    cmd = seen["mic_cmd"]
    assert cmd[cmd.index("--capture-device") + 1] == ROOM_MIC
    assert ok, msg


def _pulse_ready() -> bool:
    if not shutil.which("pactl"):
        return False
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "tlwu_mic" in out


def test_tone_played_into_the_room_sink_reaches_tlwu_mic_source(tmp_path):
    """Device plumbing of mic-room.yml: the player subprocess into tlwu-Room, tlwu's MicSource on tlwu-Mic."""
    if not _pulse_ready():
        if os.environ.get("TLWU_REQUIRE_PULSE") == "1":
            pytest.fail("TLWU_REQUIRE_PULSE=1 but no PulseAudio source tlwu_mic (mic-room.yml setup step)")
        pytest.skip("no PulseAudio server with the mic-room.yml devices")
    from tinglongwu.capture.mic import MicSource

    sr, secs = 16000, 3.0
    t = np.arange(int(sr * secs)) / sr
    wav = tmp_path / "tone.wav"
    sf.write(str(wav), (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), sr)
    src = MicSource(ROOM_MIC)
    got: list[np.ndarray] = []
    stop, first = threading.Event(), threading.Event()

    def record():
        for blk in src.frames():
            got.append(np.asarray(blk, np.float32).reshape(-1))
            first.set()
            if stop.is_set():
                break

    th = threading.Thread(target=record, daemon=True)
    th.start()
    assert first.wait(30), "MicSource yielded no audio from tlwu-Mic"
    time.sleep(0.5)  # the recorder is running before the tone starts
    p = subprocess.run([sys.executable, str(SCRIPT), "play", str(wav), "--device", ROOM_OUT],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-500:]
    assert ROOM_OUT in p.stdout
    time.sleep(1.0)  # the end of the tone is still in the PulseAudio / recorder buffers
    stop.set()
    th.join(10)
    src.close()
    x = np.concatenate(got) if got else np.zeros(0, np.float32)
    frames = x[: len(x) // 320 * 320].reshape(-1, 320)  # 20 ms frames
    loud = int((np.sqrt((frames ** 2).mean(axis=1)) > 0.1).sum())  # the tone's RMS is 0.21, silence ~0
    assert src.listening == [ROOM_MIC]
    # >= 90 % of the tone's 20 ms frames come back at tone level
    assert loud >= 0.9 * secs * 50, f"{loud} tone frames of {int(secs * 50)}"
