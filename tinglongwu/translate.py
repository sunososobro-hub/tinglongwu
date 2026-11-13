"""Translation interface and zh post-processing helpers (the engine is Hy-MT2, see hymt.py)."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

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
    from . import lang as lang_patches
    from .langfollow import has_kana

    call = translator.draft if draft else translator.translate
    if src != tgt:
        text = lang_patches.pre_mt(src, text)  # #390: per-language source fixes (tinglongwu/lang/<src>/)
    for _ in range(1 if draft else 2):
        tr = call(text, src, tgt)
        if tgt == "ja" or not has_kana(tr.text):
            tr.text = lang_patches.post_mt(tgt, tr.text, src)
            return tr
    return None

# s2twp maps a few common words to Taiwan terms that are wrong in general speech (通过->透過 "through
# means of", 支持->支援 "support = tech support"). Keep s2twp (it gives 軟體/網路/程式/記憶體 ...) and shield
# these phrases from it: each (simplified, traditional) pair is swapped for a private-use placeholder
# before conversion and restored to the plain traditional form afterwards.
# 被发 -> 被髮: OpenCC's STPhrases table has "被发" (classical 被髮左衽 "hair loose"), so passive 被发明/被发射/
# 被发送/被发布 ... come out as 被髮明 etc. Passive 被+發 is always 發 in modern text, so shield it too.
# 家具有 -> 傢俱有: STPhrases has 家具 (furniture) so "音乐家具有" (musicians have) comes out 音樂傢俱有; 家具有 is
# always 家+具有 in running text. 连接 -> 連線 is the network sense; as a plain "connect/link" (连接两个半球) it is 連接.
_KEEP = (("通过", "通過"), ("支持", "支持"), ("被发", "被發"), ("家具有", "家具有"), ("连接", "連接"))
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
# learned by OPUS from subtitle corpora (#43). In bilingual subtitle files such a block starts the *other*
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
