"""ASR engine selection with a whisper.cpp Vulkan option (#782, part 1 of #747).

cuda > vulkan > cpu, same order and TLWU_DEVICE override as the MT backend (mt_backend.choose_backend). The Vulkan
recognizer loads its backend lazily (the binaries ship in the optional Vulkan pack, part 2); any init failure
falls back to the CPU engine with exactly one stderr line, never a crash.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from . import mt_backend
from .asr import FasterWhisperRecognizer, Recognizer, Transcript


def _load_backend(pack_dir: Path, model_dir: Path, size: str):
    """Load the whisper.cpp Vulkan binding from the pack. Part 2 ships it; until then this raises ImportError."""
    raise ImportError(f"whisper.cpp Vulkan binding not available in {pack_dir}")


class WhisperCppVulkanRecognizer(Recognizer):
    """Recognizer backed by whisper.cpp built with Vulkan. `backend.transcribe(audio, language)` must return
    (text, lang, no_speech_prob, avg_logprob) or None for no speech."""

    def __init__(self, models_dir: Path, size: str = "small", lang: str = "auto", pack_dir: Path | None = None,
                 backend_loader=None):
        self.lang = None if lang == "auto" else lang
        self.size = size
        self._models_dir = Path(models_dir)
        self._pack_dir = pack_dir or mt_backend.vulkan_pack_dir()
        self._loader = backend_loader or _load_backend
        self._backend = None
        self._load()  # fail early so the caller can fall back; the heavy import is still lazy until here

    def _load(self):
        if self._backend is None:
            self._backend = self._loader(self._pack_dir, self._models_dir, self.size)
        return self._backend

    def transcribe(self, audio: np.ndarray) -> Transcript | None:
        r = self._load().transcribe(audio, self.lang)
        if not r or not str(r[0]).strip():
            return None
        text, lang, no_speech, logprob = r
        return Transcript(text=str(text).strip(), lang=lang or self.lang or "", no_speech=no_speech, avg_logprob=logprob)


def choose_asr_backend(*, cuda: bool, vulkan: bool, env=None) -> str:
    """"cuda" | "vulkan" | "cpu" (pure): TLWU_DEVICE=vulkan|cuda|cpu forces a usable backend."""
    return mt_backend.choose_backend(cuda=cuda, vulkan=vulkan, env=env)


def select_recognizer(models_dir, size="small", lang="auto", *, cuda: bool | None = None, vulkan: bool | None = None,
                      env=None, cpu_factory=None, vulkan_factory=None, warn=lambda m: print(m, file=sys.stderr),
                      **kw):
    """Build the ASR engine. CUDA present -> faster-whisper (GPU); no CUDA + Vulkan pack -> whisper.cpp Vulkan;
    Vulkan init raises -> the CPU engine plus one warning. Returns (recognizer, backend used)."""
    if cuda is None:
        from .device import cuda_device_count
        cuda = cuda_device_count() > 0
    if vulkan is None:
        vulkan = mt_backend.vulkan_available()
    cpu_factory = cpu_factory or (lambda device: FasterWhisperRecognizer(models_dir, size, lang, device, **kw))
    vulkan_factory = vulkan_factory or (lambda: WhisperCppVulkanRecognizer(models_dir, size, lang))
    backend = choose_asr_backend(cuda=cuda, vulkan=vulkan, env=env)
    if backend == "vulkan":
        try:
            return vulkan_factory(), "vulkan"
        except Exception as e:  # noqa: BLE001 - missing binding, driver / ICD, out of memory
            warn(f"tlwu: Vulkan ASR init failed ({type(e).__name__}: {e}); using CPU for ASR.")
            return cpu_factory("cpu"), "cpu"
    return cpu_factory("cuda" if backend == "cuda" else "cpu"), backend
