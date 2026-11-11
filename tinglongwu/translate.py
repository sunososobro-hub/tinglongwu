"""Translation. Default route src -> en -> tgt with OPUS-MT models (Argos packages) on CTranslate2."""
from __future__ import annotations

import json
import re
import time
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


def translate_checked(translator: "Translator", text: str, src: str, tgt: str, draft: bool = False) -> Translation | None:
    """translate()/draft() that never lets Japanese through untranslated (#86): kana in a non-Japanese target
    means the engine echoed (part of) the source. One retry (Hy-MT2 samples, so a second try usually works);
    None when it still leaks. Drafts are not retried (the next draft comes in a moment anyway)."""
    from .langfollow import has_kana

    call = translator.draft if draft else translator.translate
    for _ in range(1 if draft else 2):
        tr = call(text, src, tgt)
        if tgt == "ja" or not has_kana(tr.text):
            return tr
    return None


# s2twp maps a few common words to Taiwan terms that are wrong in general speech (通过->透過 "through
# means of", 支持->支援 "support = tech support"). Keep s2twp (it gives 軟體/網路/程式/記憶體 ...) and shield
# these phrases from it: each (simplified, traditional) pair is swapped for a private-use placeholder
# before conversion and restored to the plain traditional form afterwards.
# 被发 -> 被髮: OpenCC's STPhrases table has "被发" (classical 被髮左衽 "hair loose"), so passive 被发明/被发射/
# 被发送/被发布 ... come out as 被髮明 etc. Passive 被+發 is always 發 in modern text, so shield it too.
_KEEP = (("通过", "通過"), ("支持", "支持"), ("被发", "被發"))
_PH = {simp: chr(0xE000 + i) for i, (simp, _t) in enumerate(_KEEP)}


def zh_convert(text: str, variant: str) -> str:
    """Post-hoc script conversion: tw = Traditional (Taiwan phrases, OpenCC s2twp + _KEEP fixes); cn = Simplified."""
    from opencc import OpenCC

    text = text.replace("妳", "你")  # LLMs sometimes write the feminine 妳; always use the neutral 你
    if variant != "tw":
        return OpenCC("t2s").convert(text)
    for simp, trad in _KEEP:
        ph = _PH[simp]
        text = text.replace(simp, ph).replace(trad, ph)
    out = OpenCC("s2twp").convert(text)
    for simp, trad in _KEEP:
        out = out.replace(_PH[simp], trad)
    return out


_RARE_OK_AFTER = "子孔云曾孟曰"  # 曰 is real classical Chinese ("子曰"); elsewhere it is an OPUS en-zh glitch for 吧


def _common_cjk(ch: str) -> bool:
    """Common-character test with no extra data: GB2312 (~6.7k simplified) or Big5 level 1 (5401 traditional)."""
    try:
        ch.encode("gb2312")
        return True
    except UnicodeEncodeError:
        pass
    try:
        b = ch.encode("big5")
    except UnicodeEncodeError:
        return False
    return len(b) == 2 and 0xA440 <= (b[0] << 8 | b[1]) <= 0xC67E


def zh_garbage(text: str) -> bool:
    """True if `text` has CJK ideographs outside the common set (OPUS en-zh emits e.g. 珃瓳 for "Wow."), or a stray 曰."""
    for i, ch in enumerate(text):
        if "\u0370" <= ch <= "\u03ff" or "\u0400" <= ch <= "\u04ff" or "\u3040" <= ch <= "\u30ff":
            return True  # Greek / Cyrillic / Kana never belong in zh output (OPUS: "And life" -> 臨Τネ)
        if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿":
            if not _common_cjk(ch) or (ch == "曰" and (i == 0 or text[i - 1] not in _RARE_OK_AFTER)):
                return True
    return False


def zh_guard(src_text: str, out: str, max_words: int = 4) -> str:
    """Short interjection whose zh output is garbage: show the source text instead (better than 珃瓳)."""
    if len(src_text.split()) <= max_words and len(src_text) <= 40 and zh_garbage(out):
        return src_text
    return out


_FULLWIDTH = {",": "，", ".": "。", "!": "！", "?": "？", ";": "；", ":": "："}
_SENT = re.compile(r"(?<=[.!?。！？…])\s+")


def cjk_clean(text: str) -> str:
    """CJK target: drop spaces between CJK chars and use full-width punctuation after CJK."""
    text = re.sub(r"(?<=[^ -~]) (?=[^ -~])", "", text)
    text = re.sub(r"(?<=[^ -~])[ ]*([,.!?;:])[ ]*", lambda m: _FULLWIDTH[m.group(1)], text)
    return re.sub(r"(?<=[^ -~]) (?=[^ -~])", "", text)


_UNK = re.compile(r"<unk>|[\"'“”‘’「」『』]unk[\"'“”‘’「」『』]", re.I)


def strip_unk(text: str) -> str:
    """Remove SentencePiece unknown-token artifacts: a literal `<unk>`, and the quoted "unk" that the next
    translation step makes out of it. Tidies the spaces that the removal leaves behind."""
    if "unk" not in text.lower():
        return text
    out = _UNK.sub(" ", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?，。！？；：、])", r"\1", out)
    return out.strip()


# OPUS en-zh glitches that have nothing to do with the source: a "(c)" / "©" prefix ("countries." -> "(c) 各國。")
# and Wikipedia-style "(英語：Multiple OutPUT)" asides learned from its training data.
_COPY = re.compile(r"[(（]\s*[cC]\s*[)）]|©")
_LEAD_LETTER = re.compile(r"^\s*[(（][A-Za-z][)）]\s*")
_WIKI_ASIDE = re.compile(r"\s*[(（]\s*(?:英[語语文]|English)\s*[:：][^()（）]*[)）]")


# ASS/SSA subtitle override blocks ({\fnArial\fs16}, {\an8}, {\c&H00FFFF&}) and their line-break escapes (\N \n \h),
# learned by OPUS / M2M from subtitle corpora (#43). In bilingual subtitle files such a block starts the *other*
# subtitle line, so the text after a block in the middle of the output is a sentence unrelated to the source.
_ASS_BLOCK = re.compile(r"\{\\[^{}]*\}")
_ASS_ESC = re.compile(r"\\[Nnh]")


def strip_subtitle_codes(out: str, *sources: str) -> str:
    """Remove subtitle style codes from MT output (unless the source has them). A leading block is just dropped;
    at a block after some text, the rest of the line is dropped too (hallucinated next subtitle line, #43)."""
    if _ASS_BLOCK.search(" ".join(sources)):
        return out
    m = _ASS_BLOCK.search(out)
    if not m:
        return out
    head = _ASS_ESC.sub(" ", out[:m.start()]).strip()
    if head:
        return head
    rest = out[m.end():]
    return strip_subtitle_codes(rest, *sources) if _ASS_BLOCK.search(rest) else _ASS_ESC.sub(" ", rest).strip()


def zh_artifacts(out: str, *sources: str) -> str:
    """Strip MT artifacts from zh output unless the source line itself has them."""
    out = strip_subtitle_codes(out, *sources)
    src = " ".join(sources)
    if not _COPY.search(src):
        out = _COPY.sub("", out)
    if not _LEAD_LETTER.match(sources[0] if sources else ""):
        out = _LEAD_LETTER.sub("", out)
    out = _WIKI_ASIDE.sub("", out)
    return re.sub(r"^[\s,，、:：]+", "", out).strip()


# ALL-CAPS acronyms (MIMO, OFDM, LTE, 5G) that OPUS en-zh turns into words ("What is MIMO?" -> 什麼是馬里奧).
# Each is swapped for a number before en->zh (numbers are copied through) and put back afterwards; if a number
# does not come back intact the unprotected translation is used instead, so this never makes a line worse.
_ACRO = re.compile(r"(?<![\w-])(?=[A-Z0-9]*[A-Z][A-Z0-9]*[A-Z])[A-Z0-9]{2,6}(?![\w-])")
_CAPS_WORD = re.compile(r"[A-Z][A-Z-]+")
_PH_BASE = 9301
_ACRO_SKIP = {"OK", "TV", "US", "USA", "UK", "EU", "UN"}  # plain words with good Chinese renderings


def protect_acronyms(text: str) -> tuple[str, dict[str, str]]:
    """Replace stand-alone acronyms with numeric placeholders. Acronyms inside a run of ALL-CAPS words
    ("MULTIPLE INPUT MULTIPLE OUTPUT" - shouted ASR text, not an acronym) are left alone."""
    toks = text.split()
    words = [t.strip(".,!?;:()\"'") for t in toks]
    caps = [bool(_CAPS_WORD.fullmatch(w)) for w in words]
    shouted = [False] * len(toks)
    i = 0
    while i < len(toks):  # runs of ALL-CAPS words; a run with a long word is shouting, not acronyms
        j = i
        while j < len(toks) and caps[j]:
            j += 1
        if j > i and any(len(w) > 6 for w in words[i:j]):
            shouted[i:j] = [True] * (j - i)
        i = max(j, i + 1)
    keep = [m.group(0) for t, sh in zip(toks, shouted) if not sh
            for m in _ACRO.finditer(t) if m.group(0) not in _ACRO_SKIP]
    mapping: dict[str, str] = {}
    n = _PH_BASE
    for a in dict.fromkeys(keep):
        while str(n) in text:
            n += 1
        mapping[str(n)] = a
        n += 1
    for ph, a in mapping.items():
        text = re.sub(rf"(?<![\w-]){re.escape(a)}(?![\w-])", ph, text)
    return text, mapping


def restore_acronyms(out: str, mapping: dict[str, str]) -> str | None:
    """Put the acronyms back; None if any placeholder was lost or mangled by the model."""
    for ph, a in mapping.items():
        if len(re.findall(rf"(?<!\d){ph}(?!\d)", out)) != 1:
            return None
        out = re.sub(rf"(?<!\d){ph}(?!\d)", a, out)
    return out


class OpusStep:
    """One direction, e.g. ru->en, from models/opus/ru-en/ (ct2 model + sentencepiece)."""

    def __init__(self, models_dir: Path, src: str, tgt: str, device: str = "cpu"):
        d = require(models_dir / "opus" / f"{src}-{tgt}", f"Translation model {src}->{tgt} (pair '{src}-{tgt}')",
                    f"--pair {src}-{tgt}   # or --langs/--to, see README")
        import ctranslate2
        import sentencepiece

        from .threads import ct2_kwargs

        self.tr = ctranslate2.Translator(str(d / "model"), device=device,
                                         compute_type=compute_type(device, "mt"), **ct2_kwargs(device))
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
        return strip_unk(self.join.join(self._detok(r.hypotheses[0][cut:]) for r in res))

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
        self.last_timing: list[tuple[str, float]] = []  # (pair, seconds) of the last translate(), --debug-latency
        self.glossary = None  # tinglongwu.glossary.Glossary (set by the CLI): `~` fixes on the zh output
        self._zh_words: dict[str, str] = {}

    def _step(self, s: str, t: str):
        if (s, t) not in self._steps:
            self._steps[(s, t)] = self._factory(s, t)
        return self._steps[(s, t)]

    def _to(self, text: str, tgt: str, timing: list) -> str:
        """en -> tgt; for zh with acronym protection (falls back to the plain translation)."""
        if tgt == "zh":
            prot, mapping = protect_acronyms(text)
            if mapping:
                back = restore_acronyms(self._timed("en", tgt, prot, timing), mapping)
                if back is not None:
                    return back
        return self._timed("en", tgt, text, timing)

    def _zh_word(self, w: str) -> str:
        if w not in self._zh_words:
            self._zh_words[w] = zh_convert(w, self.zh_variant)
        return self._zh_words[w]

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        if src == tgt:
            return Translation(None, text)
        pivot = None
        timing = []
        if src == "en":
            out = self._to(text, tgt, timing)
        elif tgt == "en":
            out = self._timed(src, tgt, text, timing)
        else:
            pivot = self._timed(src, "en", text, timing)
            out = self._to(pivot, tgt, timing)
        self.last_timing = timing
        if tgt == "zh":
            out = zh_artifacts(zh_convert(out, self.zh_variant), text, pivot or "")
            if self.glossary:
                out = self.glossary.fix_output(out, text, pivot or "", convert=self._zh_word)
            out = zh_guard(text, out)
            if not out and text.strip():  # nothing left but MT artifacts (#43): show the source line
                out = text
        return Translation(pivot, out)

    def _timed(self, s: str, t: str, text: str, timing: list) -> str:
        step = self._step(s, t)
        t0 = time.monotonic()
        out = step(text)
        timing.append((f"{s}-{t}", time.monotonic() - t0))
        return out

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
        out = strip_unk(("" if tgt in ("zh", "ja") else " ").join(
            self._sp.decode(r.hypotheses[0][1:]).strip() for r in res))
        if tgt in ("zh", "ja"):
            out = cjk_clean(out)
        if tgt == "zh":
            out = zh_guard(text, strip_subtitle_codes(zh_convert(out, self.zh_variant), text)) or text
        return Translation(None, out)
