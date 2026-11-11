"""Qwen3 LLM translation engine (llama.cpp, GGUF Q4_K_M): direct src -> target, no English pivot.

Engine selection (--engine auto) looks at FREE VRAM after Whisper is loaded and walks a fallback chain
qwen3-8b -> qwen3-4b -> OPUS. Thinking mode is disabled (empty <think> block in the prompt) and any
<think> output is stripped.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .models import ModelNotFound, require
from .repeat import collapse_repeats
from .translate import Translation, Translator, zh_convert

# Free-VRAM thresholds in MiB (measured: Qwen3-8B Q4_K_M + 2048 ctx ~ 5.1 GB, 4B ~ 3 GB; margin included).
QWEN8B_MIN_FREE_MIB = 5800
QWEN4B_MIN_FREE_MIB = 3500

ENGINES = ("auto", "hymt", "opus")  # #276: Qwen / m2m are no longer user choices
# Hy-MT2-1.8B Q4_K_M uses ~1.5 GB VRAM (#66 benchmark; hwprobe.MT_VRAM_MIB) + 0.5 GB headroom (hwprobe.HEADROOM_MIB)
HYMT_MIN_FREE_MIB = 2000


@dataclass(frozen=True)
class QwenSpec:
    name: str
    dirname: str
    filename: str
    repo: str
    min_free_mib: int


QWEN_SPECS = {
    "qwen3-8b": QwenSpec("qwen3-8b", "qwen3-8b", "Qwen3-8B-Q4_K_M.gguf", "Qwen/Qwen3-8B-GGUF",
                         QWEN8B_MIN_FREE_MIB),
    "qwen3-4b": QwenSpec("qwen3-4b", "qwen3-4b", "Qwen3-4B-Q4_K_M.gguf", "Qwen/Qwen3-4B-GGUF",
                         QWEN4B_MIN_FREE_MIB),
}

LANG_NAMES = {"en": "English", "zh": "Chinese", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
              "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
              "ar": "Arabic", "hi": "Hindi", "tr": "Turkish", "vi": "Vietnamese", "th": "Thai",
              "id": "Indonesian", "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch"}

# Context: previous source lines + their translations are shown to the model so split sentences read
# coherently. Short and capped (characters ~ tokens/2 for CJK, so ~300 chars is well under N_CTX).
CONTEXT_LINES = 2
CONTEXT_MAX_CHARS = 300
CONTEXT_NOTE = ("Earlier lines are context only; do not translate or repeat them. "
                "Translate ONLY the final line (it may continue the previous one).")

STOP = ["<|im_end|>", "<|im_start|>", "<|endoftext|>"]
N_CTX = 2048


def free_vram_mib() -> int | None:
    """Free memory (MiB) on GPU 0 via nvidia-smi (no extra deps); None if it cannot be determined."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001 - no driver / no nvidia-smi
        return None


def model_path(models_dir: Path, name: str) -> Path:
    s = QWEN_SPECS[name]
    return models_dir / s.dirname / s.filename


def choose_engine(requested: str, device: str, models_dir: Path, free_mib=free_vram_mib,
                  cpu_choice=None) -> tuple[str, str]:
    """Return (engine, reason); engine in hymt2 | hymt2-cpu | opus (| the explicit Qwen/m2m engines #283/#284 delete).

    auto (#276, owner 11/11): Hy-MT2 1.8B is the one default engine on every machine. CPU (~0.93 s/sentence on an
    i5-13500, accepted by the owner) and a GPU with less than HYMT_MIN_FREE_MIB free both run it on the CPU
    (hymt2-cpu). Never Qwen, never an OPUS fallback: a missing Hy-MT2 GGUF is a setup error (make_translator).
    `cpu_choice` (the #131 speed test) is no longer consulted; the parameter stays for old callers.
    """
    if requested == "opus":
        return "opus", "requested"
    if requested == "m2m":
        return "m2m", "requested"
    if requested == "hymt":
        return "hymt2", "requested"
    if requested == "qwen":
        return "qwen3-8b", "requested"
    if requested == "qwen4b":
        return "qwen3-4b", "requested"
    if device != "cuda":
        return "hymt2-cpu", "default engine; no usable CUDA GPU (CPU mode)"
    free = free_mib()
    if free is None:
        return "hymt2-cpu", "default engine; could not read free VRAM, running it on the CPU"
    if free < HYMT_MIN_FREE_MIB:
        return "hymt2-cpu", f"default engine; {free} MiB VRAM free < {HYMT_MIN_FREE_MIB} MiB, running it on the CPU"
    return "hymt2", f"default engine; {free} MiB VRAM free >= {HYMT_MIN_FREE_MIB} MiB"


def build_system_prompt(src: str, tgt: str, zh_variant: str = "tw") -> str:
    s = LANG_NAMES.get(src, src)
    if tgt == "zh":
        dest = ("Traditional Chinese (繁體中文) as written in Taiwan, using natural Taiwan vocabulary and "
                "full-width punctuation" if zh_variant == "tw" else
                "Simplified Chinese (简体中文) with full-width punctuation")
    else:
        dest = LANG_NAMES.get(tgt, tgt)
    return (f"You are a professional translator. Translate the user's {s} text into {dest}. "
            "Output only the translation, nothing else.")


def build_user(text: str, context=(), glossary_note: str = "") -> str:
    """User turn: optional glossary note, context pairs (src, translation), then the line to translate."""
    note = f"{glossary_note}\n" if glossary_note else ""
    if not context:
        return f"{note}[Translate]\n{text}" if note else text
    ctx = "\n".join(f"{a} => {b}" for a, b in context)
    return f"{note}{CONTEXT_NOTE}\n[Earlier]\n{ctx}\n[Translate]\n{text}"


def trim_context(history, max_chars: int = CONTEXT_MAX_CHARS, lines: int = CONTEXT_LINES):
    """Last `lines` pairs, dropping the oldest until the total length fits `max_chars`."""
    h = list(history)[-lines:]
    while h and sum(len(a) + len(b) for a, b in h) > max_chars:
        h.pop(0)
    return h


def build_prompt(system: str, text: str, context=(), glossary_note: str = "") -> str:
    # Qwen3 ChatML; the empty <think> block turns thinking mode off.
    user = build_user(text, context, glossary_note)
    return (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n\n")


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


DRAFT_MAX_TOKENS = 64


def max_tokens_for(text: str) -> int:
    """Output budget proportional to input length (a translation rarely exceeds ~2 tokens per source char)."""
    return max(32, min(400, 2 * len(text) + 16))


def _load_llama(path: Path, gpu: bool):
    """Import llama.cpp (pip llama-cpp-python, CUDA build) and load a GGUF.

    A build compiled for instructions this CPU lacks (issue #74: AVX-512 wheel on an AVX2 CPU) raises
    llamacheck.LlamaBuildError with the fix, instead of a bare 'Windows Error 0xc000001d'."""
    from . import llamacheck
    from .device import register_cuda_libs

    register_cuda_libs()
    try:
        import llama_cpp
    except (ImportError, OSError, RuntimeError) as e:  # not installed, or llama.dll / a CUDA DLL missing
        raise RuntimeError(f"Qwen engine needs llama-cpp-python (CUDA build): {e}. "
                           f"To fix: {llamacheck.FIX_HINT}") from e
    if sys.platform == "win32":
        lib = Path(llama_cpp.__file__).parent / "lib"
        if lib.is_dir():
            os.add_dll_directory(str(lib))
    llamacheck.check_build(llama_cpp)
    from llama_cpp import Llama

    try:
        return Llama(model_path=str(path), n_gpu_layers=-1 if gpu else 0, n_ctx=N_CTX, flash_attn=gpu,
                     verbose=False)
    except OSError as e:
        err = llamacheck.explain(e)
        if err is e:
            raise
        raise err from e


class QwenTranslator(Translator):
    """Direct src -> tgt translation with a local Qwen3 GGUF; OpenCC afterwards when tgt is zh."""

    def __init__(self, models_dir: Path, name: str = "qwen3-8b", zh_variant: str = "tw",
                 device: str = "cuda", loader=None):
        self.name, self.zh_variant, self.device = name, zh_variant, device
        self.path = require(model_path(models_dir, name), f"Qwen model '{name}'", "--qwen")
        self._loader = loader or _load_llama
        self.llm = None
        self.history: deque = deque(maxlen=CONTEXT_LINES)  # (source, translation) of previous lines
        self._pair: tuple[str, str] | None = None
        self.glossary = None  # tinglongwu.glossary.Glossary (set by the CLI) or None

    def _load(self):
        if self.llm is None:
            self.llm = self._loader(self.path, self.device == "cuda")

    def preload(self, src: str = "", tgt: str = "") -> None:
        self._load()

    def draft(self, text: str, src: str, tgt: str) -> Translation:
        """Draft: same prompt (with current context) but short output, and history is not updated."""
        return self.translate(text, src, tgt, draft=True)

    def translate(self, text: str, src: str, tgt: str, draft: bool = False) -> Translation:
        if src == tgt or not text.strip():
            return Translation(None, text)
        text = collapse_repeats(text)  # safety net against ASR repetition loops
        self._load()
        if self._pair != (src, tgt):  # language changed: old context would mislead
            self.history.clear()
            self._pair = (src, tgt)
        ctx = trim_context(self.history)
        note = self.glossary.prompt_block(text, *(a for a, _ in ctx)) if self.glossary else ""
        prompt = build_prompt(build_system_prompt(src, tgt, self.zh_variant), text, ctx, note)
        r = self.llm.create_completion(prompt, max_tokens=min(max_tokens_for(text), DRAFT_MAX_TOKENS) if draft else max_tokens_for(text), temperature=0.0, stop=STOP)
        out = clean_output(r["choices"][0]["text"])
        if tgt == "zh":
            out = zh_convert(out, self.zh_variant)
        if out and not draft:
            self.history.append((text.strip(), out))
        return Translation(None, out)


def build_qwen_with_fallback(models_dir: Path, first: str, zh_variant: str, device: str,
                             warn=lambda m: print(m, file=sys.stderr), factory=QwenTranslator,
                             errors: list | None = None):
    """Load `first`; on load failure (e.g. CUDA OOM) step down 8b -> 4b. Returns (translator|None, name|None).

    (None, None) means every Qwen tier failed: the caller falls back to OPUS and must say so; the reason of
    each failed tier is appended to `errors` ("name: error").
    A missing GGUF raises ModelNotFound (clear setup hint) instead of being swallowed.
    """
    chain = ["qwen3-8b", "qwen3-4b"]
    todo = chain[chain.index(first):]
    for i, name in enumerate(todo):
        try:
            tr = factory(models_dir, name, zh_variant, device)
            tr.preload()
            return tr, name
        except ModelNotFound:
            raise
        except Exception as e:  # noqa: BLE001 - OOM / driver / llama.cpp load errors
            if errors is not None:
                errors.append(f"{name}: {e}")
            if i + 1 < len(todo):
                warn(f"tlwu: {name} failed to load ({str(e)[:200]}); trying {todo[i + 1]}.")
    return None, None
