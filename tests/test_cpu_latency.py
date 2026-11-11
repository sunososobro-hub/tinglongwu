"""#186 CPU speak-mode latency: timing line, endpoint settings, fast Whisper flags, --allow-monitor."""
import threading

import numpy as np
import pytest

from tinglongwu import device
from tinglongwu.reverse import proto
from tinglongwu.reverse.live import capture_segments, check_mic_feedback
from tinglongwu.reverse.logic import Lat, live_endpoint, timing_line


def test_timing_line_format_and_total():
    t = {"vad": 0.6, "asr": 1.1, "mt": 0.2, "tts": 0.8}
    assert timing_line(t) == "   [vad 0.60 | stt 1.10 | mt 0.20 | tts 0.80 | total 2.70 s]"
    assert timing_line({"asr": 1.0, "mt": 0.5}, tts=False) == "   [vad - | stt 1.00 | mt 0.50 | total 1.50 s]"
    assert "tts -" in timing_line({"vad": 0.4, "asr": 1.0, "mt": 0.5})


def test_live_endpoint_cpu_faster_cuda_unchanged_override_wins():
    assert live_endpoint("cpu", env={}) == (0.4, 0.25)
    assert live_endpoint("cuda", env={}) == (0.6, 0.5)
    assert live_endpoint("cpu", 0.3, env={}) == (0.3, 0.25)
    assert live_endpoint("cuda", 0.8, env={}) == (0.8, 0.5)
    assert live_endpoint("cpu", env={"TLWU_END_SILENCE": "0.5"}) == (0.5, 0.25)
    assert live_endpoint("cpu", env={"TLWU_END_SILENCE": "junk"}) == (0.4, 0.25)


def test_cpu_threads():
    assert device.cpu_threads(16, {}) == 8
    assert device.cpu_threads(4, {}) == 4
    assert device.cpu_threads(64, {}) == 8
    assert device.cpu_threads(16, {"TLWU_CPU_THREADS": "6"}) == 6


def test_capture_segments_records_endpoint_pause_as_vad():
    class Seg:
        last_pause = 0.0

        def segments(self, frames):
            self.last_pause = 0.45
            yield np.ones(4, np.float32)

    class Src:
        def frames(self):
            return iter(())

    lat = Lat()
    out = list(capture_segments(Src(), Seg(), threading.Event(), lat))
    assert len(out) == 1 and lat.take() == {"vad": 0.45}


def test_allow_monitor_only_with_flag_and_never_for_tlwu():
    with pytest.raises(ValueError):
        check_mic_feedback("Monitor of Built-in Audio", None, same_name_ok=True)
    check_mic_feedback("Monitor of Built-in Audio", None, same_name_ok=True, allow_monitor=True)
    with pytest.raises(ValueError):
        check_mic_feedback("Monitor of tlwu_sink", None, same_name_ok=True, allow_monitor=True)
    with pytest.raises(ValueError):
        check_mic_feedback("tlwu_mic", None, same_name_ok=True, allow_monitor=True)


def test_proto_has_flags_default_off():
    src = open(proto.__file__, encoding="utf-8").read()
    assert '"--allow-monitor", action="store_true"' in src and '"--end-silence"' in src
