"""Translation engine registry and the llama.cpp helpers shared by the GGUF engines (Hy-MT2).

`ENGINE_REGISTRY` maps each `--engine` choice to the engine name `cli.make_translator` builds; adding an engine
(e.g. Hy-MT2 7B, #279) is one entry here plus its builder. `auto` is not in the registry: see `choose_engine`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import weakref
from pathlib import Path

# --engine choice -> engine name. hymt7b (#279), ollama and fm (#356, a local Ollama server / Apple's on-device
# model) are opt-in only: choose_engine("auto") never returns them. opus was removed (#471): Hy-MT2 is the only
# built-in engine; `REMOVED_ENGINES` says how to fix an old command line.
ENGINE_REGISTRY = {"hymt": "hymt2", "hymt7b": "hymt2-7b", "ollama": "ollama", "fm": "fm"}
ENGINES = ("auto", *ENGINE_REGISTRY)
REMOVED_ENGINES = {"opus": "the OPUS/Argos pivot was removed (#471): Hy-MT2 is the only built-in translation engine. "
                           "Use --engine auto (or drop --engine); install Hy-MT2 once with `tlwu-setup --hymt`"}


def engine_choice(v: str) -> str:
    """argparse type for --engine: a removed engine exits 2 with how to fix it (not just 'invalid choice')."""
    import argparse

    if v in REMOVED_ENGINES:
        raise argparse.ArgumentTypeError(REMOVED_ENGINES[v])
    return v
# Hy-MT2-1.8B Q4_K_M uses ~1.5 GB VRAM (#66 benchmark; hwprobe.MT_VRAM_MIB) + 0.5 GB headroom (hwprobe.HEADROOM_MIB)
HYMT_MIN_FREE_MIB = 2000
N_CTX = 2048


def free_vram_mib() -> int | None:
    """Free memory (MiB) on GPU 0 via nvidia-smi (no extra deps); None if it cannot be determined."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001 - no driver / no nvidia-smi
        return None


def choose_engine(requested: str, device: str, models_dir: Path, free_mib=free_vram_mib,
                  cpu_choice=None) -> tuple[str, str]:
    """Return (engine, reason); engine is a registry value, or hymt2-cpu for auto without a usable GPU.

    auto (#276, owner 11/11): Hy-MT2 1.8B is the one default engine on every machine. CPU (~0.93 s/sentence on an
    i5-13500, accepted by the owner) and a GPU with less than HYMT_MIN_FREE_MIB free both run it on the CPU
    (hymt2-cpu). Never an OPUS fallback: a missing Hy-MT2 GGUF is a setup error (make_translator).
    `cpu_choice` (the #131 speed test) is no longer consulted; the parameter stays for old callers.
    """
    if requested in ENGINE_REGISTRY:
        return ENGINE_REGISTRY[requested], "requested"
    if device != "cuda":
        return "hymt2-cpu", "default engine; no usable CUDA GPU (CPU mode)"
    free = free_mib()
    if free is None:
        return "hymt2-cpu", "default engine; could not read free VRAM, running it on the CPU"
    if free < HYMT_MIN_FREE_MIB:
        return "hymt2-cpu", f"default engine; {free} MiB VRAM free < {HYMT_MIN_FREE_MIB} MiB, running it on the CPU"
    return "hymt2", f"default engine; {free} MiB VRAM free >= {HYMT_MIN_FREE_MIB} MiB"


_THINK = re.compile(r"<think>.*?</think>", re.S)
_LOOP = re.compile(r"(.{2,120}?)\1{2,}", re.S)
_LABEL = re.compile(r"^\s*(?:\[?(?:translation|translated text|translate)\]?|翻譯|翻译|譯文|译文)\s*[:：]\s*", re.I)
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "「": "」", "『": "』", "«": "»"}
_UNDERSCORE_IN_WORD = re.compile(r"(?<=[A-Za-z0-9])_+(?=[A-Za-z0-9])")


def clean_output(text: str) -> str:
    text = _THINK.sub("", text)
    if "<think>" in text:  # unclosed
        text = text.split("<think>")[0]
    text = text.replace("</think>", "")
    text = _LOOP.sub(r"\1", text)  # collapse degenerate repetition ("abcabcabcabc..." -> "abc")
    text = " ".join(text.split())
    text = _LABEL.sub("", text)
    text = re.sub(r"^(?:#+|>|[-*]\s)\s*", "", text)  # markdown heading / quote / bullet
    text = text.replace("**", "").replace("`", "").replace("*", "")
    text = _UNDERSCORE_IN_WORD.sub(" ", text).replace("_", "")  # "可可_chan" -> "可可 chan"
    if len(text) >= 2 and _QUOTE_PAIRS.get(text[0]) == text[-1] and text[1:-1].count(text[0]) == 0:
        text = text[1:-1].strip()  # whole output wrapped in quotes
    return text.strip()


def max_tokens_for(text: str) -> int:
    """Output budget proportional to input length (a translation rarely exceeds ~2 tokens per source char)."""
    return max(32, min(400, 2 * len(text) + 16))


def load_llama(path: Path, gpu: bool):
    """Import llama.cpp (pip llama-cpp-python) and load a GGUF.

    A build compiled for instructions this CPU lacks (issue #74: AVX-512 wheel on an AVX2 CPU) raises
    llamacheck.LlamaBuildError with the fix, instead of a bare 'Windows Error 0xc000001d'."""
    from . import llamacheck
    from . import threads as thread_budget

    try:
        llama_cpp = llamacheck.import_llama()  # CUDA build, or the bundled CPU build when CUDA cannot load (#398)
    except (ImportError, OSError, RuntimeError) as e:  # not installed, or llama.dll / a CUDA DLL missing
        raise RuntimeError(f"Hy-MT2 needs llama-cpp-python: {e}. "
                           f"To fix: {llamacheck.FIX_HINT}") from e
    if sys.platform == "win32":
        lib = Path(llama_cpp.__file__).parent / "lib"
        if lib.is_dir():
            os.add_dll_directory(str(lib))
    llamacheck.check_build(llama_cpp)
    from llama_cpp import Llama

    try:
        t = thread_budget.current()  # #250: explicit, so listen+speak can share the CPU (listen-only = old defaults)
        return Llama(model_path=str(path), n_gpu_layers=-1 if gpu else 0, n_ctx=N_CTX, flash_attn=gpu,
                     n_threads=t["mt"], n_threads_batch=t["mt_batch"], verbose=False)
    except OSError as e:
        err = llamacheck.explain(e)
        if err is e:
            raise
        raise err from e


class SharedLlama:
    """One loaded GGUF used by several translators (#285: listen+speak in one process). llama.cpp contexts are
    not thread-safe, so every completion runs under one lock: the two sides take turns, never overlap."""

    def __init__(self, llm):
        self.llm = llm
        self.lock = threading.Lock()

    def create_chat_completion(self, *a, **k):
        with self.lock:
            return self.llm.create_chat_completion(*a, **k)

    def __getattr__(self, name):
        return getattr(self.llm, name)


_SHARED: "weakref.WeakValueDictionary" = weakref.WeakValueDictionary()
_SHARED_LOCK = threading.Lock()


def shared_llama(path: Path, gpu: bool, loader=None) -> SharedLlama:
    """`loader(path, gpu)` once per (model file, gpu, loader) in this process; later callers get the same
    instance while any translator still holds it. The load itself runs under a lock, so two sides starting at
    the same time still load the model once."""
    loader = loader or load_llama
    key = (str(Path(path).resolve()), bool(gpu), loader)
    with _SHARED_LOCK:
        llm = _SHARED.get(key)
        if llm is None:
            llm = SharedLlama(loader(path, gpu))
            _SHARED[key] = llm
        return llm
