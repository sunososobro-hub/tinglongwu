import builtins
import io
import os

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.capture import FileSource
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Result, TextSink, TranscriptSink
from tinglongwu.translate import PivotTranslator

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND


class FakeASR(Recognizer):
    def transcribe(self, audio):
        return Transcript("привет мир", "ru")


def fake_steps(s, t):
    return {("ru", "en"): lambda x: "hello world", ("en", "zh"): lambda x: "你好世界软件"}[(s, t)]


def test_no_disk_writes(wav, tmp_path, monkeypatch):
    before = sorted(os.listdir(tmp_path))
    real_open, real_os_open = builtins.open, os.open

    def guarded_open(f, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+") and "__pycache__" not in str(f):
            raise AssertionError(f"write open of {f}")
        return real_open(f, mode, *a, **k)

    def guarded_os_open(p, flags, *a, **k):
        if flags & WRITE_FLAGS and "__pycache__" not in str(p):
            raise AssertionError(f"os.open write of {p}")
        return real_os_open(p, flags, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    out = io.StringIO()
    run(FileSource(str(wav)), Segmenter(chunk=1.0, use_vad=False), FakeASR(),
        PivotTranslator(tmp_path, "tw", fake_steps), TextSink(out))
    assert out.getvalue().count("[RU] привет мир") >= 2
    assert "[ZH] 你好世界軟體" in out.getvalue()
    assert sorted(os.listdir(tmp_path)) == before


def test_silence_skipped(tmp_path):
    class Silent:
        def frames(self):
            yield np.zeros(16000 * 2, np.float32)

        def close(self):
            pass

    out = io.StringIO()
    run(Silent(), Segmenter(chunk=1.0, use_vad=False), FakeASR(),
        PivotTranslator(tmp_path, "tw", fake_steps), TextSink(out))
    assert out.getvalue() == ""


def test_vad_segmenter_pause_split():
    sr = 16000
    sig = np.concatenate([np.ones(2 * sr), np.zeros(2 * sr), np.ones(sr)]).astype(np.float32)
    vad = lambda a: [(int(s), int(e)) for s, e in _spans(a)]  # noqa: E731

    def _spans(a):
        m = np.abs(a) > 0.5
        edges = np.flatnonzero(np.diff(np.concatenate([[0], m.astype(int), [0]])))
        return list(zip(edges[::2], edges[1::2]))

    frames = [sig[i:i + 8000] for i in range(0, len(sig), 8000)]
    segs = list(Segmenter(chunk=10, vad_fn=vad).segments(frames))
    assert len(segs) == 2


def test_transcript_only_format(tmp_path):
    out = io.StringIO()
    TranscriptSink(out).write(Result("ru", "привет\nмир", None, ""))
    assert out.getvalue() == "привет мир\n"
