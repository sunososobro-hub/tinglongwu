"""Tencent Hy-MT2-1.8B (Apache-2.0, Q4_K_M GGUF, llama.cpp): direct multilingual MT used for Japanese.

OPUS has no usable zh<->ja models (opus-mt-en-jap is Bible-trained; ja-en emits <unk>), so every pair that
involves Japanese goes through this model, direct src -> tgt with no English pivot. Everything else keeps
using whatever engine was chosen (OPUS pivot on CPU, Qwen on GPU). `JaRouter` does that routing and falls
back to the OPUS pivot for ja -> X when the GGUF (or llama-cpp-python) is missing.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .qwen import _load_llama, clean_output, max_tokens_for
from .repeat import collapse_repeats
from .translate import Translation, Translator, strip_unk, zh_convert

HYMT_DIR = "hymt2-1.8b"
HYMT_FILE = "Hy-MT2-1.8B-Q4_K_M.gguf"
HYMT_REPO = "tencent/Hy-MT2-1.8B-GGUF"
# Model card sampling for the 1.8B model.
SAMPLING = dict(temperature=0.7, top_p=0.6, top_k=20, repeat_penalty=1.05)

ZH_NAMES = {"zh": "中文", "ja": "日语", "en": "英语", "ru": "俄语", "ko": "韩语", "de": "德语", "fr": "法语",
            "es": "西班牙语"}
EN_NAMES = {"zh": "Chinese", "ja": "Japanese", "en": "English", "ru": "Russian", "ko": "Korean",
            "de": "German", "fr": "French", "es": "Spanish"}


def hymt_path(models_dir: Path) -> Path:
    return Path(models_dir) / HYMT_DIR / HYMT_FILE


def build_prompt(text: str, src: str, tgt: str) -> str:
    """Model-card template; the Chinese one when Chinese is on either side, else the English one."""
    if "zh" in (src, tgt):
        return (f"将以下文本翻译成{ZH_NAMES.get(tgt, tgt)}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}")
    return (f"Translate the following text into {EN_NAMES.get(tgt, tgt)}.\n"
            f"Note that you should only output the translated result without any additional explanation:\n\n{text}")


class HyMtTranslator(Translator):
    """Direct src -> tgt with a local Hy-MT2 GGUF; OpenCC afterwards when tgt is zh. No context memory
    (the card's single-message template), so history/_pair exist only for interface compatibility."""

    def __init__(self, models_dir: Path, zh_variant: str = "tw", device: str = "cpu", loader=None):
        from .models import require

        self.zh_variant, self.device = zh_variant, device
        self.path = require(hymt_path(models_dir), "Hy-MT2 translation model (Japanese)", "--hymt")
        self._loader = loader or _load_llama
        self.llm = None
        self.history: list = []
        self._pair = None

    def _load(self):
        if self.llm is None:
            self.llm = self._loader(self.path, self.device == "cuda")

    def preload(self, src: str = "", tgt: str = "") -> None:
        self._load()

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        if src == tgt or not text.strip():
            return Translation(None, text)
        text = collapse_repeats(text)
        self._load()
        r = self.llm.create_chat_completion(
            [{"role": "user", "content": build_prompt(text, src, tgt)}],
            max_tokens=max_tokens_for(text), **SAMPLING)
        out = strip_unk(clean_output(r["choices"][0]["message"]["content"] or ""))
        if tgt == "zh":
            out = zh_convert(out, self.zh_variant)
        return Translation(None, out)


class JaRouter(Translator):
    """Pairs with Japanese -> Hy-MT2 (direct); every other pair -> `base`. If Hy-MT2 is not installed (or fails
    to load) ja -> X falls back to base's pivot; X -> ja then raises (there is no OPUS route to Japanese)."""

    def __init__(self, base: Translator, models_dir: Path, zh_variant: str = "tw", device: str = "cpu",
                 factory=HyMtTranslator, warn=lambda m: print(m, file=sys.stderr)):
        self.base, self.zh_variant = base, zh_variant
        self.history: list = []
        self._pair = None
        self._warn = warn
        self.hy = None
        self.hy_error = ""
        if hymt_path(models_dir).exists():
            try:
                self.hy = factory(models_dir, zh_variant, device)
            except Exception as e:  # noqa: BLE001
                self.hy_error = str(e)
        else:
            self.hy_error = f"{hymt_path(models_dir)} missing (run `tlwu-setup --hymt`)"

    def _use_hy(self, src: str, tgt: str) -> bool:
        return "ja" in (src, tgt) and self.hy is not None

    def _guard(self, src: str, tgt: str) -> None:
        if tgt == "ja" and self.hy is None:
            raise RuntimeError(f"Japanese output needs the Hy-MT2 model: {self.hy_error}")

    def _fallback(self, e: Exception) -> None:
        self._warn(f"tlwu: Hy-MT2 failed ({str(e)[:160]}); ja -> X falls back to the OPUS pivot.")
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
        elif "ja" in (src, tgt):
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
