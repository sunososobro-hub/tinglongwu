"""Tencent Hy-MT2-1.8B (Apache-2.0, Q4_K_M GGUF, llama.cpp): direct multilingual MT.

It is the only built-in translation engine (#276, #471: the OPUS/Argos pivot is gone from the listen path):
`JaRouter(None, ..., all_pairs=True)` translates every pair whose two languages it knows (`EN_NAMES`) direct
src -> tgt, and any other pair is an error with the reason (no silent engine switch). The reverse/speak side
builds the same router with its own no-fallback base (#331).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .engines import clean_output, max_tokens_for
from .engines import load_llama as _load_llama
from .engines import shared_llama
from .repeat import collapse_repeats
from .translate import Translation, Translator, strip_unk, zh_convert

HYMT_DIR = "hymt2-1.8b"
HYMT_FILE = "Hy-MT2-1.8B-Q4_K_M.gguf"
HYMT_REPO = "tencent/Hy-MT2-1.8B-GGUF"
# #279: opt-in Hy-MT2 7B Q4_K_M (~4.8 GB, GPUs with >= 8 GB); same family, prompt and sampling as 1.8B.
# Never chosen by auto: only `--engine hymt7b` after `tlwu-setup --hymt7b`.
HYMT_SIZES = {"1.8b": ("hymt2-1.8b", HYMT_FILE, "--hymt"), "7b": ("hymt2-7b", "Hy-MT2-7B-Q4_K_M.gguf", "--hymt7b")}
# Model card sampling for the 1.8B model.
SAMPLING = dict(temperature=0.7, top_p=0.6, top_k=20, repeat_penalty=1.05)



def sampling() -> dict:
    """SAMPLING, with temperature overridable by env TLWU_HYMT_TEMP (e.g. 0 for repeatable terminology)."""
    s = dict(SAMPLING)
    try:
        s["temperature"] = float(os.environ["TLWU_HYMT_TEMP"])
    except (KeyError, ValueError):
        pass
    return s


# #335: it/pt/vi/th/id are on the Hy-MT2 model card's language list, so they go direct instead of the OPUS pivot.
ZH_NAMES = {"zh": "中文", "ja": "日语", "en": "英语", "ru": "俄语", "ko": "韩语", "de": "德语", "fr": "法语",
            "es": "西班牙语", "it": "意大利语", "pt": "葡萄牙语", "vi": "越南语", "th": "泰语", "id": "印尼语"}
EN_NAMES = {"zh": "Chinese", "ja": "Japanese", "en": "English", "ru": "Russian", "ko": "Korean",
            "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
            "vi": "Vietnamese", "th": "Thai", "id": "Indonesian"}


def hymt_path(models_dir: Path, size: str = "1.8b") -> Path:
    d, f, _ = HYMT_SIZES[size]
    return Path(models_dir) / d / f


def setup_flag(size: str = "1.8b") -> str:
    """The tlwu-setup flag that downloads this size."""
    return HYMT_SIZES[size][2]


def build_prompt(text: str, src: str, tgt: str, terms: list[tuple[str, str]] | None = None) -> str:
    """Model-card template; the Chinese one when Chinese is on either side, else the English one.
    `terms` (source, translation) pairs are prepended in the card's terminology-reference form; none = unchanged."""
    if terms:
        ref = "\n".join(f"{a} 翻译成 {b}" for a, b in terms)
        return f"参考下面的翻译：\n{ref}\n\n" + build_prompt(text, src, tgt)
    if "zh" in (src, tgt):
        return (f"将以下文本翻译成{ZH_NAMES.get(tgt, tgt)}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}")
    return (f"Translate the following text into {EN_NAMES.get(tgt, tgt)}.\n"
            f"Note that you should only output the translated result without any additional explanation:\n\n{text}")


def zh_script_guard(src_text: str, out: str, max_words: int = 4) -> str:
    """Short line whose "Chinese" output still contains kana / Cyrillic / Greek (left untranslated or echoed):
    show the source text instead. Same size limits as translate.zh_guard, but only the wrong-script test: its
    rare-ideograph test is tuned for OPUS garbage and would reject valid Taiwan interjections (欸, 齁)."""
    if len(src_text.split()) <= max_words and len(src_text) <= 40 and any(
            "\u0370" <= c <= "\u03ff" or "\u0400" <= c <= "\u04ff" or "\u3040" <= c <= "\u30ff" for c in out):
        return src_text
    return out


class HyMtTranslator(Translator):
    """Direct src -> tgt with a local Hy-MT2 GGUF; OpenCC afterwards when tgt is zh. No context memory
    (the card's single-message template), so history/_pair exist only for interface compatibility."""

    def __init__(self, models_dir: Path, zh_variant: str = "tw", device: str = "cpu", loader=None,
                 size: str = "1.8b"):
        from .models import require

        self.zh_variant, self.device, self.size = zh_variant, device, size
        self.path = require(hymt_path(models_dir, size), f"Hy-MT2 {size.upper()} translation model", setup_flag(size))
        self._loader = loader or _load_llama
        self.llm = None
        self.history: list = []
        self._pair = None
        self.glossary = None  # tinglongwu.glossary.Glossary (set by the CLI): `~` fixes on the zh output (#174)
        self._zh_words: dict[str, str] = {}

    def _zh_word(self, w: str) -> str:
        if w not in self._zh_words:
            self._zh_words[w] = zh_convert(w, self.zh_variant)
        return self._zh_words[w]

    def _terms(self, text: str, tgt: str) -> list[tuple[str, str]]:
        """Glossary entries occurring in the sentence, as (source, zh translation); only for zh output."""
        if tgt != "zh" or not self.glossary:
            return []
        return [(e.main, self._zh_word(e.target)) for e in self.glossary.relevant(text) if e.target]

    def _load(self):
        if self.llm is None:
            self.llm = shared_llama(self.path, self.device == "cuda", self._loader)  # one instance per process (#285)

    def preload(self, src: str = "", tgt: str = "") -> None:
        self._load()

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        if src == tgt or not text.strip():
            return Translation(None, text)
        text = collapse_repeats(text)
        self._load()
        r = self.llm.create_chat_completion(
            [{"role": "user", "content": build_prompt(text, src, tgt, self._terms(text, tgt))}],
            max_tokens=max_tokens_for(text), **sampling())
        out = strip_unk(clean_output(r["choices"][0]["message"]["content"] or ""))
        if tgt == "zh":
            out = zh_convert(out, self.zh_variant)
            if self.glossary:
                out = self.glossary.fix_output(out, text, convert=self._zh_word)
            out = zh_script_guard(text, out)
        return Translation(None, out)


class JaRouter(Translator):
    """Pairs with Japanese -> Hy-MT2 (direct); every other pair -> `base`. If Hy-MT2 is not installed (or fails
    to load) ja -> X falls back to base's pivot; X -> ja then raises (there is no OPUS route to Japanese).

    all_pairs=True (Hy-MT2 as the main engine, #66): every pair whose two languages Hy-MT2 knows goes direct
    through it; a pair with an unknown language, or any pair after Hy-MT2 failed, goes to `base`.
    base=None (the listen path since #471: Hy-MT2 is the only engine, no OPUS pivot): such a pair raises
    RuntimeError with the reason instead of switching engines."""

    def __init__(self, base: Translator | None, models_dir: Path, zh_variant: str = "tw", device: str = "cpu",
                 factory=HyMtTranslator, warn=lambda m: print(m, file=sys.stderr), all_pairs: bool = False,
                 size: str = "1.8b"):
        self.base = base if base is not None else _NoFallback(self)
        self.zh_variant, self.all_pairs = zh_variant, all_pairs
        self._pivot = base is not None
        self.history: list = []
        self._pair = None
        self._warn = warn
        self.hy = None
        self.hy_error = ""
        self._glossary = None
        if hymt_path(models_dir, size).exists():
            try:  # the 1.8B call keeps the old 3-argument factory signature
                self.hy = (factory(models_dir, zh_variant, device) if size == "1.8b"
                           else factory(models_dir, zh_variant, device, size=size))
            except Exception as e:  # noqa: BLE001
                self.hy_error = str(e)
        else:
            self.hy_error = f"{hymt_path(models_dir, size)} missing (run `tlwu-setup {setup_flag(size)}`)"

    @property
    def glossary(self):
        return self._glossary

    @glossary.setter
    def glossary(self, g) -> None:
        """One glossary for both routes (#174): Hy-MT2 and the OPUS pivot both apply its `~` fixes."""
        self._glossary = g
        for t in (self.hy, self.base):
            if t is not None and hasattr(t, "glossary"):
                t.glossary = g

    def _use_hy(self, src: str, tgt: str) -> bool:
        if self.hy is None:
            return False
        if self.all_pairs:
            return src in EN_NAMES and tgt in EN_NAMES
        return "ja" in (src, tgt)

    def _guard(self, src: str, tgt: str) -> None:
        if tgt == "ja" and self.hy is None:
            raise RuntimeError(f"Japanese output needs the Hy-MT2 model: {self.hy_error}")

    def _fallback(self, e: Exception) -> None:
        what = "translation" if self.all_pairs else "ja -> X"
        if self._pivot:
            self._warn(f"tlwu: WARNING: Hy-MT2 failed ({str(e)[:160]}); {what} falls back to the OPUS pivot "
                       "(lower translation quality).")
        else:
            self._warn(f"tlwu: WARNING: Hy-MT2 failed ({str(e)[:160]}); there is no fallback engine (#471).")
        self.hy_error, self.hy = str(e), None

    def preload(self, src: str, tgt: str) -> None:
        self._guard(src, tgt)
        if self._use_hy(src, tgt):
            try:
                self.hy.preload()
                return
            except Exception as e:  # noqa: BLE001
                if tgt == "ja":
                    raise
                self._fallback(e)
        elif "ja" in (src, tgt) and self._pivot:
            self._warn(f"tlwu: Hy-MT2 unavailable for Japanese ({self.hy_error}); using OPUS pivot ja -> en -> zh.")
        self.base.preload(src, tgt)

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        self._guard(src, tgt)
        if self._use_hy(src, tgt):
            try:
                return self.hy.translate(text, src, tgt)
            except Exception as e:  # noqa: BLE001
                if tgt == "ja":
                    raise
                self._fallback(e)
        return self.base.translate(text, src, tgt)

    def draft(self, text: str, src: str, tgt: str) -> Translation:
        return self.translate(text, src, tgt)


class _NoFallback(Translator):
    """JaRouter's base when there is no pivot (#471): every pair Hy-MT2 does not take is an error with the reason."""

    def __init__(self, router: JaRouter):
        self.router = router
        self.glossary = None

    def _fail(self, src: str, tgt: str):
        r = self.router
        why = (f"Hy-MT2 is not available ({r.hy_error[:160]})" if r.hy is None
               else f"Hy-MT2 does not translate {src} -> {tgt}")
        raise RuntimeError(f"{why}; there is no fallback engine (#471). Install or repair it with "
                           "`tlwu-setup --hymt`")

    def preload(self, src: str, tgt: str) -> None:
        self._fail(src, tgt)

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        self._fail(src, tgt)
