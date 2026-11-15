"""Per-sentence speaker labels for --speakers (#219/#222): sherpa-onnx embedding + online clustering.

Opt-in. sherpa-onnx is imported only when a SpeakerLabeler is built, so the default path never loads it.
Everything runs locally; the model comes from `tlwu-setup --speaker-model` (models/speaker/).
"""
from __future__ import annotations

import time
from pathlib import Path

from .speakers import SpeakerClusterer

SAMPLE_RATE = 16000
MODEL_NAME = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SPEAKER_DIR = "speaker"


class SherpaEmbedder:
    """seg (float32 mono 16 kHz) -> embedding list. Needs the optional sherpa-onnx package + model file."""

    def __init__(self, mdir, num_threads: int = 1):
        path = Path(mdir) / SPEAKER_DIR / MODEL_NAME
        if not path.is_file():
            raise RuntimeError(f"--speakers: speaker model missing ({path}); run: tlwu-setup --speaker-model")
        try:
            import sherpa_onnx
        except ImportError as e:
            raise RuntimeError("--speakers needs sherpa-onnx: pip install --require-hashes -r "
                               "requirements-speaker.txt") from e
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=num_threads, provider="cpu")
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def __call__(self, seg):
        s = self._ex.create_stream()
        s.accept_waveform(sample_rate=SAMPLE_RATE, waveform=seg)
        s.input_finished()
        return list(self._ex.compute(s))


class SpeakerLabeler:
    """label(seg) -> (speaker label, seconds spent on embedding + clustering)."""

    def __init__(self, embed, clusterer: SpeakerClusterer | None = None, clock=time.monotonic):
        self.embed = embed
        self.clusterer = clusterer or SpeakerClusterer()
        self.clock = clock

    def label(self, seg) -> tuple[str, float]:
        t0 = self.clock()
        secs = len(seg) / SAMPLE_RATE
        if secs < self.clusterer.min_seconds:  # too short to embed: skip the model, reuse the last speaker
            name = self.clusterer.assign([], secs)
        else:
            name = self.clusterer.assign(self.embed(seg), secs)
        return name, self.clock() - t0


def make_labeler(mdir) -> SpeakerLabeler:
    return SpeakerLabeler(SherpaEmbedder(mdir))


def available(mdir) -> bool:
    """0.3.8 GUI: True when sherpa-onnx is importable and the speaker model file exists (checked without loading)."""
    import importlib.util

    try:
        return (Path(mdir) / SPEAKER_DIR / MODEL_NAME).is_file() and importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False


class AsyncSpeakerLabeler:
    """0.3.8 GUI listen side: speaker ID off the subtitle critical path.

    label(seg) returns at once with a token ("spk#N", never UNKNOWN) and queues a copy of the audio; a worker
    thread embeds + clusters in order and stores the real label under the token (result(token): the label,
    None while pending / unknown). on_ready() is called after each result so the GUI can redraw. A full queue
    drops the segment (its label stays unknown) rather than slowing anything down."""

    def __init__(self, embed, clusterer: SpeakerClusterer | None = None, on_ready=None, maxsize: int = 8):
        import queue
        import threading

        self._inner = SpeakerLabeler(embed, clusterer)
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)
        self._res: dict[str, str | None] = {}
        self._n = 0
        self.on_ready = on_ready
        self._queue_mod = queue
        threading.Thread(target=self._work, daemon=True, name="tlwu-speaker-id").start()

    def label(self, seg):
        self._n += 1
        token = f"spk#{self._n}"
        try:
            self._q.put_nowait((token, seg.copy() if hasattr(seg, "copy") else seg))
        except self._queue_mod.Full:
            pass  # too busy: this line just gets no label
        return token, 0.0

    def result(self, token):
        return self._res.get(token)

    def close(self):
        self._q.put(None)

    def _work(self):
        from .speakers import UNKNOWN

        while True:
            item = self._q.get()
            if item is None:
                return
            token, seg = item
            try:
                who, _s = self._inner.label(seg)
            except Exception:  # noqa: BLE001 - speaker ID must never hurt subtitles
                who = UNKNOWN
            self._res[token] = None if who == UNKNOWN else who
            if self.on_ready is not None:
                try:
                    self.on_ready()
                except Exception:  # noqa: BLE001
                    pass


def make_async_labeler(mdir, on_ready=None) -> AsyncSpeakerLabeler | None:
    """The GUI's labeler, or None when sherpa-onnx / the model is missing or fails to load (no labels, no error)."""
    try:
        if not available(mdir):
            return None
        return AsyncSpeakerLabeler(SherpaEmbedder(mdir), on_ready=on_ready)
    except Exception:  # noqa: BLE001
        return None
