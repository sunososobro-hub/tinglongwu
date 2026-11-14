"""#849: scripts/cable_loopback_check.py proves VB-CABLE routing with a signal check (RMS above
SILENCE_RMS_THRESHOLD on CABLE Output while a tone plays into CABLE Input). Runs with a fake soundcard here; the
audio-routing workflow runs it against the real virtual device on windows-latest."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("cable_loopback_849", ROOT / "scripts" / "cable_loopback_check.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)


class FakeCable:
    """A soundcard stand-in: what is played into the cable's input comes out of its output (or nothing: broken)."""

    def __init__(self, routed=True, noise=0.0):
        self.buf, self.routed, self.noise = [], routed, noise
        cable = self

        class Spk:
            name = "CABLE Input (VB-Audio Virtual Cable)"

            def player(self, samplerate, channels):
                sr = samplerate
                class P:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def play(self, data):  # like soundcard: returns once the data has been played
                        import time
                        if cable.routed:
                            cable.buf.append(np.asarray(data, np.float32))
                        time.sleep(len(data) / sr)
                return P()

        class Mic:
            name = "CABLE Output (VB-Audio Virtual Cable)"

            def recorder(self, samplerate, channels, blocksize):
                class R:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def record(self, numframes):  # clocked like a real device: no drift against wall time
                        import time
                        self.n = getattr(self, "n", 0) + numframes
                        self.t0 = getattr(self, "t0", time.monotonic())
                        time.sleep(max(0.0, self.t0 + self.n / samplerate - time.monotonic()))
                        out = np.full(numframes, cable.noise, np.float32)
                        if cable.buf:
                            data = cable.buf[0][:numframes]
                            cable.buf[0] = cable.buf[0][numframes:]
                            if not cable.buf[0].size:
                                cable.buf.pop(0)
                            out[:data.size] += data
                        return out.reshape(-1, 1)
                return R()

        self.spk, self.mic = Spk(), Mic()

    def all_speakers(self):
        return [type("S", (), {"name": "Speakers (Realtek)"})(), self.spk]

    def all_microphones(self, include_loopback=False):
        return [self.mic]

    def get_speaker(self, name):
        return self.spk

    def get_microphone(self, name):
        return self.mic


def test_threshold_is_a_named_constant_below_the_tone_level():
    assert 0 < cl.SILENCE_RMS_THRESHOLD < cl.rms(cl.tone()) / 10


def test_routed_tone_passes_and_lists_devices():
    out = cl.check(cl.CABLE_IN, cl.CABLE_OUT, FakeCable())
    assert out["ok"], out["reason"]
    assert out["tone_rms"] > cl.SILENCE_RMS_THRESHOLD >= out["baseline_rms"]
    assert "CABLE Input (VB-Audio Virtual Cable)" in out["speakers"]
    assert out["microphones"] == ["CABLE Output (VB-Audio Virtual Cable)"]


def test_wrong_device_name_fails():
    out = cl.check("CABLE Inputx", cl.CABLE_OUT, FakeCable())
    assert not out["ok"] and "device not found: playback 'CABLE Inputx'" in out["reason"]


def test_broken_routing_is_silence_and_fails():
    out = cl.check(cl.CABLE_IN, cl.CABLE_OUT, FakeCable(routed=False))
    assert not out["ok"] and out["reason"].startswith("FAIL silence")


def test_noise_before_the_tone_fails():
    out = cl.check(cl.CABLE_IN, cl.CABLE_OUT, FakeCable(noise=0.2))
    assert not out["ok"] and "noise before the tone" in out["reason"]


@pytest.mark.parametrize("base, tone_rms, ok", [(0.0, 0.3, True), (0.0, 0.005, False), (0.05, 0.3, False)])
def test_verdict(base, tone_rms, ok):
    assert cl.verdict(base, tone_rms)[0] is ok


def test_find_device_is_case_insensitive_substring():
    names = ["Speakers (Realtek)", "CABLE Input (VB-Audio Virtual Cable)"]
    assert cl.find_device(names, "cable input") == names[1]
    assert cl.find_device(names, "CABLE Output") is None
