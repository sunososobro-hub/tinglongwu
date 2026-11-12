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
