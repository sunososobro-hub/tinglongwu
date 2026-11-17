"""ASR engine selection with a whisper.cpp Vulkan option (#782, part 1 of #747).

cuda > vulkan > cpu, same order and TLWU_DEVICE override as the MT backend (mt_backend.choose_backend). The Vulkan
recognizer loads its backend lazily (#783: vulkan-pack/whisper/ holds the whisper.cpp Vulkan build, part 2); any init failure
falls back to the CPU engine with exactly one stderr line, never a crash.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

import numpy as np

from . import mt_backend
from .asr import FasterWhisperRecognizer, Recognizer, Transcript


WHISPER_DIR = "whisper"  # vulkan-pack/whisper/: the whisper.cpp build, apart from llama.cpp's own ggml libraries
WHISPER_LIB = "tlwu-whisper.dll" if sys.platform == "win32" else "libtlwu-whisper.so"
WHISPER_ABI = 1  # = TLWU_WHISPER_ABI in scripts/whisper_vulkan/tlwu_whisper.cpp
MODEL_DIR = "whisper-cpp"  # models/whisper-cpp/ggml-<size>.bin (whisper.cpp needs its own ggml model files)
_TEXT_MAX = 8192


class WhisperCppBackend:
    """ctypes binding of the pack's tlwu-whisper library (#783). Only plain types cross the boundary; the C side
    builds whisper_full_params against the pinned whisper.h. One whisper context, calls serialized (not thread safe)."""

    def __init__(self, lib_path: Path, model_path: Path, use_gpu: bool = True, load=ctypes.CDLL, threads=None):
        self._lock = threading.Lock()  # first: close() (also via __del__) takes it even when __init__ raised
        self._ctx = None
        if not Path(lib_path).is_file():
            raise ImportError(f"whisper.cpp Vulkan library not found: {lib_path}")
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"whisper.cpp model not found: {model_path}")
        lib = load(str(lib_path))
        lib.tlwu_whisper_abi.restype = ctypes.c_int
        abi = lib.tlwu_whisper_abi()
        if abi != WHISPER_ABI:
            raise ImportError(f"{Path(lib_path).name}: C API version {abi}, the app needs {WHISPER_ABI}")
        lib.tlwu_whisper_init.restype = ctypes.c_void_p
        lib.tlwu_whisper_init.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.tlwu_whisper_free.argtypes = [ctypes.c_void_p]
        lib.tlwu_whisper_transcribe.restype = ctypes.c_int
        f = ctypes.POINTER(ctypes.c_float)
        lib.tlwu_whisper_transcribe.argtypes = [ctypes.c_void_p, f, ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                                                ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, f, f]
        self._lib = lib
        self._threads = threads or min(4, os.cpu_count() or 1)
        self._ctx = lib.tlwu_whisper_init(str(model_path).encode("utf-8"), 1 if use_gpu else 0)
        if not self._ctx:
            raise RuntimeError(f"whisper.cpp could not load {Path(model_path).name}")

    def transcribe(self, audio: np.ndarray, language: str | None):
        """(text, lang, no_speech_prob, avg_logprob), or None when whisper.cpp found no text."""
        pcm = np.ascontiguousarray(audio, dtype=np.float32)
        text = ctypes.create_string_buffer(_TEXT_MAX)
        lang = ctypes.create_string_buffer(16)
        no_speech, logprob = ctypes.c_float(0.0), ctypes.c_float(0.0)
        with self._lock:
            if not self._ctx:
                raise RuntimeError("whisper.cpp context is closed")
            rc = self._lib.tlwu_whisper_transcribe(
                self._ctx, pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), int(pcm.size),
                (language or "auto").encode("ascii"), self._threads, text, _TEXT_MAX, lang, 16,
                ctypes.byref(no_speech), ctypes.byref(logprob))
        if rc != 0:
            raise RuntimeError(f"whisper.cpp transcribe failed (rc={rc})")
        out = text.value.decode("utf-8", "replace").strip()
        if not out:
            return None
        return out, lang.value.decode("ascii", "replace"), float(no_speech.value), float(logprob.value)

    def close(self):
        lock = getattr(self, "_lock", None)
        if lock is None:
            return
        with lock:  # never free the context while a transcribe call is using it
            ctx, self._ctx = self._ctx, None
            if ctx:
                self._lib.tlwu_whisper_free(ctx)

    __del__ = close


def _load_backend(pack_dir: Path | None, model_dir: Path, size: str, load=ctypes.CDLL):
    """The pack's whisper/<WHISPER_LIB> with models/whisper-cpp/ggml-<size>.bin. ImportError when the pack has no
    whisper build, FileNotFoundError without the model: select_recognizer then falls back to the CPU engine."""
    if pack_dir is None:
        raise ImportError("no Vulkan pack installed")
    lib = Path(pack_dir) / WHISPER_DIR / WHISPER_LIB
    return WhisperCppBackend(lib, Path(model_dir) / MODEL_DIR / f"ggml-{size}.bin", load=load)


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
