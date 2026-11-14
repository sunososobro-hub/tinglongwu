"""#21: `--file --paced` must hand the audio on in live capture's 100 ms blocks.

The Segmenter can only cut when a frame arrives. With the file's 0.5 s frames every cut rounded up to the next
0.5 s, so the --end-silence 0.5 vs 0.6 latency A/B measured the same cut time for both settings."""
from __future__ import annotations

import numpy as np

from tinglongwu import cli
from tinglongwu.capture.base import SAMPLE_RATE, AudioSource
from tinglongwu.capture.file import FileSource
from tinglongwu.stream import PacedSource, paced_live_blocks

SPEECH_S, CLIP_S = 1.0, 3.0


def _clip() -> np.ndarray:
    audio = np.zeros(int(CLIP_S * SAMPLE_RATE), np.float32)
    audio[: int(SPEECH_S * SAMPLE_RATE)] = 0.3
    return audio


class _Src(AudioSource):
    """A file-like source that hands on 0.5 s frames, like FileSource."""

    def __init__(self, audio):
        self.audio, self.closed = audio, False

    def frames(self):
        for i in range(0, len(self.audio), SAMPLE_RATE // 2):
            yield self.audio[i:i + SAMPLE_RATE // 2]

    def close(self):
        self.closed = True


def _vad(buf):  # speech = non-zero samples, no padding
    nz = np.flatnonzero(buf)
    return [(int(nz[0]), int(nz[-1]) + 1)] if len(nz) else []


def _cut_after(source_frames, end_silence) -> float:
    """Seconds of silence after the speech that had arrived when the listen segmenter cut the segment."""
    args = cli.parse_args(["--file", "x.wav"] + ([] if end_silence is None else ["--end-silence", str(end_silence)]))
    seg = cli.listen_segmenter(args)
    seg.vad, seg.pad = _vad, 0.0
    got = [0]

    def counted():
        for f in source_frames:
            got[0] += len(f)
            yield f

    next(seg.segments(counted()))
    return got[0] / SAMPLE_RATE - SPEECH_S


def _paced_frames(source):
    paced = paced_live_blocks(source)
    assert isinstance(paced, PacedSource)
    return list(paced.inner.frames())  # the blocks PacedSource releases, without waiting in real time


def test_paced_source_hands_on_100ms_blocks_of_the_whole_file():
    src = _Src(_clip())
    frames = _paced_frames(src)
    assert {len(f) for f in frames[:-1]} == {SAMPLE_RATE // 10}
    assert np.array_equal(np.concatenate(frames), src.audio)
    assert src.closed


def test_paced_file_source_decodes_a_real_wav(tmp_path):
    import soundfile as sf

    wav = tmp_path / "c.wav"
    sf.write(str(wav), _clip(), SAMPLE_RATE)
    frames = _paced_frames(FileSource(str(wav)))
    assert len(frames[0]) == SAMPLE_RATE // 10
    assert np.allclose(np.concatenate(frames), _clip(), atol=1e-4)


def _live_frames():  # what WASAPI / mic capture hands on: 100 ms blocks
    audio = _clip()
    return [audio[i:i + SAMPLE_RATE // 10] for i in range(0, len(audio), SAMPLE_RATE // 10)]


def test_paced_cut_matches_live_capture():
    for sil in (None, 0.5):
        assert _cut_after(_paced_frames(_Src(_clip())), sil) == _cut_after(_live_frames(), sil)
    default, short = _cut_after(_paced_frames(_Src(_clip())), None), _cut_after(_paced_frames(_Src(_clip())), 0.5)
    assert short < default <= 0.6 + 0.3  # default 0.6 s, checked every ~0.3 s of 100 ms blocks
    # the old 0.5 s file frames cut later than live capture would (the paced latency overstated the VAD wait)
    assert _cut_after(_Src(_clip()).frames(), None) > default


def test_run_session_paced_uses_live_blocks(monkeypatch, tmp_path):
    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod

    class _A:
        device = "cpu"
        live = False

    seen = {}
    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", lambda *a, **k: _A())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: _Src(_clip()))
    monkeypatch.setattr(pipeline_mod, "run", lambda source, *a, **k: seen.setdefault("source", source))
    args = cli.parse_args(["--transcript-only", "--file", str(tmp_path / "in.wav"), "--model", "tiny",
                           "--models-dir", str(tmp_path), "--paced"])
    cli.run_session(args)
    assert isinstance(seen["source"], PacedSource)
    assert len(next(iter(seen["source"].inner.frames()))) == SAMPLE_RATE // 10
