"""Per-sentence speaker labels for --speakers (#219/#222): sherpa-onnx embedding + online clustering.

Opt-in. sherpa-onnx is imported only when a SpeakerLabeler is built, so the default path never loads it.
Everything runs locally; the model comes from `tlwu-setup --speaker-model` (models/speaker/).
"""
from __future__ import annotations

import time
from pathlib import Path

import itertools

from .speakers import UNKNOWN as UNKNOWN_LABEL, SpeakerClusterer

_TOKENS = itertools.count(1)  # global: tokens of different labelers never collide (live toggle)

SAMPLE_RATE = 16000
MODEL_NAME = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
SPEAKER_DIR = "speaker"


class SherpaEmbedder:
    """seg (float32 mono 16 kHz) -> embedding list. Needs the optional sherpa-onnx package + model file."""

    def __init__(self, mdir, num_threads: int = 1):
        path = Path(mdir) / SPEAKER_DIR / MODEL_NAME
        if not path.is_file():
            raise RuntimeError(f"--speakers: speaker model missing ({path}); run: tlwu-setup --speaker-model")
        from .speaker_pylib import activate

        activate(mdir)  # packaged build: models/speaker/pylib on sys.path (no-op when absent)
        try:
            import sherpa_onnx
        except ImportError as e:
            raise RuntimeError("--speakers needs sherpa-onnx: pip install --require-hashes -r "
                               "requirements/requirements-speaker.txt") from e
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
    from .speaker_pylib import activate, module_present

    activate(mdir)
    return (Path(mdir) / SPEAKER_DIR / MODEL_NAME).is_file() and module_present("sherpa_onnx")


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
        self._closed = False
        self.on_ready = on_ready
        self._queue_mod = queue
        threading.Thread(target=self._work, daemon=True, name="tlwu-speaker-id").start()

    def label(self, seg):
        token = f"spk#{next(_TOKENS)}"
        try:
            self._q.put_nowait((token, seg.copy() if hasattr(seg, "copy") else seg))
        except self._queue_mod.Full:
            self._res[token] = None  # too busy: this line just gets no label (resolve() must not wait for it)
        return token, 0.0

    def result(self, token):
        return self._res.get(token)

    def resolve(self, token, timeout: float = 1.5):
        """The real label of `token` (None = unknown). Waits up to `timeout` s for the worker; a token that is not
        a "spk#N" token is returned as is. Used by the saved transcript, never by the subtitle path."""
        import time

        if not isinstance(token, str) or not token.startswith("spk#"):
            return token
        end = time.monotonic() + timeout
        while token not in self._res and time.monotonic() < end:
            time.sleep(0.01)
        return self._res.get(token)

    def close(self):
        """Stop the worker thread (idempotent); a queue that is full is emptied so the stop marker fits."""
        if self._closed:
            return
        self._closed = True
        while True:
            try:
                self._q.put_nowait(None)
                return
            except self._queue_mod.Full:
                try:
                    self._q.get_nowait()
                except self._queue_mod.Empty:
                    pass

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


def make_async_labeler(mdir, on_ready=None, clusterer: SpeakerClusterer | None = None) -> AsyncSpeakerLabeler | None:
    """The GUI's labeler, or None when sherpa-onnx / the model is missing or fails to load (no labels, no error)."""
    try:
        if not available(mdir):
            return None
        return AsyncSpeakerLabeler(SherpaEmbedder(mdir), clusterer=clusterer, on_ready=on_ready)
    except Exception:  # noqa: BLE001
        return None


class LiveSpeakers:
    """0.3.8 GUI: the pipeline's `speakers` object. It forwards to whichever labeler is attached right now, so the
    「標示說話者」 toggle can attach / detach one while the session runs. The reference is swapped under a lock and
    read once per call, so the subtitle path never stalls or sees a half-closed labeler's exception.
    Tokens are globally unique ("spk#N"), so the results of earlier labelers stay readable after a swap."""

    def __init__(self, labeler=None):
        import threading

        self._lock = threading.Lock()
        self._cur = labeler
        self._all = [labeler] if labeler is not None else []  # live labelers only (retired ones are dropped)
        self._maps: list[dict] = []  # result dicts of closed labelers: finished / still-arriving results stay readable
        self.closed = False

    def get(self):
        return self._cur

    def set(self, labeler):
        """Attach `labeler` (or None = detach). The previous one is closed (its finished results stay readable).
        After close() a new labeler is closed at once (the session is over)."""
        with self._lock:
            old, self._cur = self._cur, (None if self.closed else labeler)
            if labeler is not None and not self.closed:
                self._all.append(labeler)
        for lab in (old, labeler if self.closed else None):
            self._retire(lab)

    def _retire(self, lab):
        """Close `lab` and drop it (its model / thread are not kept); only its result dict is retained."""
        if lab is None:
            return
        try:
            lab.close()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            res = getattr(lab, "_res", None)
            if isinstance(res, dict):
                self._maps.append(res)
                self._all = [x for x in self._all if x is not lab]

    def label(self, seg):
        lab = self._cur
        if lab is None:
            return UNKNOWN_LABEL, 0.0
        try:
            return lab.label(seg)
        except Exception:  # noqa: BLE001 - never hurt subtitles
            return UNKNOWN_LABEL, 0.0

    def result(self, token):
        for res in list(self._maps):
            if res.get(token) is not None:
                return res[token]
        for lab in list(self._all):
            r = lab.result(token)
            if r is not None:
                return r
        return None

    def resolve(self, token, timeout: float = 1.5):
        if not isinstance(token, str) or not token.startswith("spk#"):
            return token
        for lab in list(self._all):
            if token in getattr(lab, "_res", {}) or lab is self._cur:
                return lab.resolve(token, timeout)
        for res in list(self._maps):
            if token in res:
                return res.get(token)
        return None

    def close(self):
        with self._lock:
            self.closed = True
            cur, self._cur = self._cur, None
            rest = list(self._all)
        for lab in rest:  # every labeler is closed and dropped (cur is among them)
            self._retire(lab)
        if cur is not None and cur not in rest:
            self._retire(cur)
