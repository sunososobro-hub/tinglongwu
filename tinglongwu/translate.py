"""Translation. Default route src -> en -> tgt with OPUS-MT models (Argos packages) on CTranslate2."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .device import compute_type
from .models import require


@dataclass
class Translation:
    pivot: str | None  # English intermediate (None when src or tgt is English)
    text: str


class Translator(ABC):
    @abstractmethod
    def translate(self, text: str, src: str, tgt: str) -> Translation: ...

    def draft(self, text: str, src: str, tgt: str) -> Translation:
        """Quick translation for --stream drafts; must not change translator state. Default: translate()."""
        return self.translate(text, src, tgt)


def zh_convert(text: str, variant: str) -> str:
    """Post-hoc script conversion: tw = Traditional (Taiwan phrases, OpenCC s2twp); cn = Simplified."""
    from opencc import OpenCC

    text = text.replace("妳", "你")  # LLMs sometimes write the feminine 妳; always use the neutral 你
    return OpenCC("s2twp" if variant == "tw" else "t2s").convert(text)


_FULLWIDTH = {",": "，", ".": "。", "!": "！", "?": "？", ";": "；", ":": "："}
_SENT = re.compile(r"(?<=[.!?。！？…])\s+")


def cjk_clean(text: str) -> str:
    """CJK target: drop spaces between CJK chars and use full-width punctuation after CJK."""
    text = re.sub(r"(?<=[^ -~]) (?=[^ -~])", "", text)
    text = re.sub(r"(?<=[^ -~])[ ]*([,.!?;:])[ ]*", lambda m: _FULLWIDTH[m.group(1)], text)
    return re.sub(r"(?<=[^ -~]) (?=[^ -~])", "", text)


class OpusStep:
    """One direction, e.g. ru->en, from models/opus/ru-en/ (ct2 model + sentencepiece)."""

    def __init__(self, models_dir: Path, src: str, tgt: str, device: str = "cpu"):
        d = require(models_dir / "opus" / f"{src}-{tgt}", f"Translation model {src}->{tgt} (pair '{src}-{tgt}')",
                    f"--pair {src}-{tgt}   # or --langs/--to, see README")
        import ctranslate2
        import sentencepiece

        self.tr = ctranslate2.Translator(str(d / "model"), device=device,
                                         compute_type=compute_type(device, "mt"))
        self.sp = sentencepiece.SentencePieceProcessor(model_file=str(d / "sentencepiece.model"))
        meta = json.loads((d / "metadata.json").read_text("utf-8")) if (d / "metadata.json").exists() else {}
        self.prefix = meta.get("target_prefix") or ""
        self.join = "" if tgt in ("zh", "ja") else " "

    def __call__(self, text: str) -> str:
        sents = [s for s in _SENT.split(text) if s.strip()]
        if not sents:
            return ""
        toks = [self.sp.encode(s, out_type=str) for s in sents]
        res = self.tr.translate_batch(
            toks, beam_size=4, max_decoding_length=256,
            target_prefix=[[self.prefix]] * len(toks) if self.prefix else None)
        cut = 1 if self.prefix else 0
        return self.join.join(self._detok(r.hypotheses[0][cut:]) for r in res)

    def _detok(self, pieces: list[str]) -> str:
        # sp.decode() on a model whose output pieces are not in the source vocab (e.g. en->zh) leaves
        # literal U+2581 markers; join the pieces ourselves instead.
        text = "".join(pieces).replace("▁", " ").strip()
        return cjk_clean(text) if self.join == "" else text


class PivotTranslator(Translator):
    """src -> en -> tgt (EN step skipped when either side is English)."""

    def __init__(self, models_dir: Path, zh_variant: str = "tw",
                 step_factory: Callable[[str, str], Callable[[str], str]] | None = None,
                 device: str = "cpu"):
        self.zh_variant = zh_variant
        self.device = device  # already resolved: "cpu" or "cuda"
        self._factory = step_factory or (lambda s, t: OpusStep(models_dir, s, t, self.device))
        self._steps: dict[tuple[str, str], Callable[[str], str]] = {}

    def _step(self, s: str, t: str):
        if (s, t) not in self._steps:
            self._steps[(s, t)] = self._factory(s, t)
        return self._steps[(s, t)]

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        if src == tgt:
            return Translation(None, text)
        pivot = None
        if src == "en" or tgt == "en":
            out = self._step(src, tgt)(text)
        else:
            pivot = self._step(src, "en")(text)
            out = self._step("en", tgt)(pivot)
        if tgt == "zh":
            out = zh_convert(out, self.zh_variant)
        return Translation(pivot, out)

    def preload(self, src: str, tgt: str) -> None:
        """Load models up front so missing models fail before capture starts."""
        pairs = [(src, tgt)] if "en" in (src, tgt) else [(src, "en"), ("en", tgt)]
        for p in pairs:
            self._step(*p)


M2M_CODES = {k: f"__{k}__" for k in ("en", "zh", "ru", "ja", "ko", "de", "fr", "es", "it", "pt", "ar", "hi",
                                     "tr", "vi", "th", "id", "uk", "pl", "nl")}
M2M_DIR = "m2m-1.2B"


def find_m2m(models_dir: Path) -> Path:
    return require(models_dir / M2M_DIR, "M2M-100 1.2B model", "--m2m")


class M2mTranslator(Translator):
    """--engine m2m: M2M-100 1.2B (CTranslate2) direct src->tgt, no English pivot. MIT licence (usable commercially).

    Loaded only from models/m2m-1.2B (tlwu-setup --m2m converts it).
    """

    SPM = "sentencepiece.bpe.model"
    TABLE = M2M_CODES

    def __init__(self, models_dir: Path, zh_variant: str = "tw", device: str = "cpu"):
        self.zh_variant = zh_variant
        self.device = device
        self.dir = find_m2m(models_dir)
        self._tr = self._sp = None

    def _load(self):
        if self._tr is None:
            import ctranslate2
            import sentencepiece

            self._tr = ctranslate2.Translator(str(self.dir / "model"), device=self.device,
                                              compute_type=compute_type(self.device, "mt"))
            self._sp = sentencepiece.SentencePieceProcessor(model_file=str(self.dir / self.SPM))

    @classmethod
    def _code(cls, lang: str) -> str:
        if lang not in cls.TABLE:
            raise RuntimeError(f"{cls.__name__}: language {lang!r} not in tlwu's code table "
                               f"({sorted(cls.TABLE)}); extend the table in tinglongwu/translate.py")
        return cls.TABLE[lang]

    def preload(self, src: str, tgt: str) -> None:
        self._code(src), self._code(tgt)
        self._load()

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        if src == tgt:
            return Translation(None, text)
        self._load()
        s, t = self._code(src), self._code(tgt)
        sents = [x for x in _SENT.split(text) if x.strip()]
        toks = [[s] + self._sp.encode(x, out_type=str) + ["</s>"] for x in sents]  # HF M2M100: __src__ + pieces + </s>
        res = self._tr.translate_batch(toks, target_prefix=[[t]] * len(toks), beam_size=4,
                                       max_decoding_length=256)
        out = ("" if tgt in ("zh", "ja") else " ").join(
            self._sp.decode(r.hypotheses[0][1:]).strip() for r in res)
        if tgt in ("zh", "ja"):
            out = cjk_clean(out)
        if tgt == "zh":
            out = zh_convert(out, self.zh_variant)
        return Translation(None, out)
