"""#1247: quiet real speech (-63 dBFS, like the FLEURS clips the #894 --file pass missed) must reach ASR;
digital silence must still be skipped before Whisper."""
import io

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink

SR = 16000


def _dbfs_noise(db: float, seconds: float = 2.0) -> np.ndarray:
    rng = np.random.default_rng(1247)
    x = rng.standard_normal(int(SR * seconds)).astype(np.float32)
    return (x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20)).astype(np.float32)


class _Src:
    def __init__(self, audio):
        self.audio = audio

    def frames(self):
        yield self.audio

    def close(self):
        pass


class _ASR(Recognizer):
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return Transcript("the lecture starts now", "en")


def _run(audio):
    asr, out = _ASR(), io.StringIO()
    run(_Src(audio), Segmenter(chunk=1.0, use_vad=False), asr, None, TextSink(out))
    return asr.calls, out.getvalue()


def test_quiet_speech_at_minus_63_dbfs_is_transcribed():
    calls, out = _run(_dbfs_noise(-63.2))  # median RMS of the 13 clips missed in bench-cpu-ab live-file (#1324)
    assert calls >= 1
    assert "the lecture starts now" in out


def test_quietest_missed_clip_level_is_transcribed():
    calls, _ = _run(_dbfs_noise(-66.0))  # quietest missed clip
    assert calls >= 1


def test_digital_silence_still_skipped():
    calls, out = _run(np.zeros(SR * 2, np.float32))
    assert calls == 0 and out == ""


def test_near_digital_silence_still_skipped():
    calls, out = _run(_dbfs_noise(-95.0))  # idle virtual cable / dither floor
    assert calls == 0 and out == ""
