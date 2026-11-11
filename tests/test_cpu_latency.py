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
    assert live_endpoint("cpu", env={}) == (0.5, 0.25)
    assert live_endpoint("cuda", env={}) == (0.6, 0.5)
    assert live_endpoint("cpu", 0.3, env={}) == (0.3, 0.25)
    assert live_endpoint("cuda", 0.8, env={}) == (0.8, 0.5)
    assert live_endpoint("cpu", env={"TLWU_END_SILENCE": "0.5"}) == (0.5, 0.25)
    assert live_endpoint("cpu", env={"TLWU_END_SILENCE": "junk"}) == (0.5, 0.25)


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


def test_proto_help_lists_latency_flags():
    """CLI behaviour: both #186 flags are real options of the speak-mode entry point."""
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "tinglongwu.reverse.proto", "--help"], capture_output=True,
                         text=True, timeout=120).stdout
    assert "--allow-monitor" in out and "--end-silence" in out and "TLWU_VAD_PAD" in out


SR = 16000


def _padded_energy_vad(audio, pad_ms=400):
    """Stand-in for Silero: one span over the loud samples, end padded by pad_ms like faster-whisper does."""
    idx = np.nonzero(np.abs(audio) > 0.01)[0]
    if not idx.size:
        return []
    return [(int(idx[0]), int(min(len(audio), idx[-1] + 1 + SR * pad_ms // 1000)))]


def _wait_after_speech(make_seg) -> tuple[float, float]:
    """Feed silence + 1.5 s 'speech' + 3 s silence in 250-sample frames, for 5 speech-end phases against the VAD
    step; median (seconds of silence fed before the segment came out, the segment's reported last_pause)."""
    waits, reps = [], []
    for lead in (0.30, 0.35, 0.40, 0.45, 0.50):
        x = np.concatenate([np.zeros(int(lead * SR)), 0.3 * np.ones(int(1.5 * SR)),
                            np.zeros(3 * SR)]).astype(np.float32)
        end, fed, seg = int((lead + 1.5) * SR), [0], make_seg()

        def frames():
            for i in range(0, len(x), 250):
                fed[0] = i + 250
                yield x[i:i + 250]

        next(iter(seg.segments(frames())))
        waits.append((fed[0] - end) / SR)
        reps.append(seg.last_pause)
    return float(np.median(waits)), float(np.median(reps))


def test_live_cpu_vad_pad_cuts_end_of_speech_wait(monkeypatch):
    """#186: the Silero 0.4 s span pad sat in front of min_silence, so CPU waited ~0.9 s while the timing line said
    ~0.5 s. Live CPU now uses a 0.1 s pad; the reported vad equals the real wait."""
    from tinglongwu import segment
    from tinglongwu.reverse.logic import live_vad_pad

    monkeypatch.setattr(segment, "silero_vad", _padded_energy_vad)
    old_wait, old_rep = _wait_after_speech(lambda: segment.Segmenter(10, True, min_silence=0.4, step=0.25))
    sil, step = live_endpoint("cpu", env={})
    new_wait, new_rep = _wait_after_speech(
        lambda: segment.Segmenter(10, True, min_silence=sil, step=step, vad_pad=live_vad_pad("cpu", env={})))
    assert old_wait >= 0.8 and old_rep < old_wait - 0.3  # before: real wait 0.8+, reported 0.4 too low
    assert new_wait <= 0.7 and abs(new_rep - new_wait) <= 0.1
    assert new_wait <= old_wait - 0.2


def test_live_vad_pad_defaults_and_env():
    from tinglongwu.reverse.logic import live_vad_pad

    assert live_vad_pad("cpu", env={}) == 0.1
    assert live_vad_pad("cuda", env={}) is None  # CUDA unchanged (library default 0.4 s)
    assert live_vad_pad("cuda", env={"TLWU_VAD_PAD": "0.2"}) == 0.2
    assert live_vad_pad("cpu", env={"TLWU_VAD_PAD": "junk"}) == 0.1
    assert live_vad_pad("cpu", env={"TLWU_VAD_PAD": "-1"}) == 0.1


def test_silero_vad_passes_pad_to_faster_whisper(monkeypatch):
    import faster_whisper.vad as fv

    from tinglongwu import segment

    seen = []
    monkeypatch.setattr(fv, "get_speech_timestamps", lambda a, opts: seen.append(opts.speech_pad_ms) or [])
    segment.Segmenter(vad_pad=0.1).vad(np.zeros(SR, np.float32))
    segment.Segmenter().vad(np.zeros(SR, np.float32))
    assert seen == [100, 400]


def test_live_main_wires_cpu_endpoint_and_pad(monkeypatch):
    """_live_main (mic speak path, text-only) builds its Segmenter with the CPU endpoint settings and the 0.1 s pad."""
    import types

    from tinglongwu import segment
    from tinglongwu.capture import mic

    made = {}

    class Seg:
        def __init__(self, chunk, use_vad, **kw):
            made.update(kw, chunk=chunk)

        def segments(self, frames):
            return iter(())

    class Mic:
        def __init__(self, name):
            pass

        def frames(self):
            return iter(())

    monkeypatch.delenv("TLWU_VAD_PAD", raising=False)
    monkeypatch.delenv("TLWU_END_SILENCE", raising=False)
    monkeypatch.setattr(segment, "Segmenter", Seg)
    monkeypatch.setattr(mic, "MicSource", Mic)
    a = types.SimpleNamespace(dev="cpu", end_silence=None, mic_name="m", chunk_s=6.0, to="en")
    off = types.SimpleNamespace(enabled=False)
    proto._live_main(a, None, None, None, off, off, False, {"items": []})
    assert made == {"chunk": 6.0, "min_silence": 0.5, "step": 0.25, "vad_pad": 0.1}
