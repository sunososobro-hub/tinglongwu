"""Topic glossary: plain-text files (a file, or a folder of *.txt) that adapt tlwu to one topic.

Format (UTF-8, one entry per line):
    частота = 頻率           source = target translation
    чистота | частота = 頻率  `|` lists extra source spellings (common mis-hearings); first is the main one
    роутер                   no `=`: plain hint term (helps Whisper, kept as written by the LLM)
    fading = 衰落 ~ 淡化 | 消散  `~` lists known wrong renderings: when the entry occurs in the sentence (source
                             or English pivot), they are replaced by the target in the output (OPUS)
    # comment
Used two ways: source terms go to Whisper (`hotwords`); entries whose source occurs in the current sentence
or its context go into the MT prompt. Number entries (`2.4 = 2.4 GHz`) also normalize "2 и 4" -> "2.4".
Fully offline; reads local files only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Callable

HOTWORDS_MAX_CHARS = 200  # Whisper prompt budget: hotwords + previous text must stay far below 224 tokens
MAX_PROMPT_ENTRIES = 8
_WORD = re.compile(r"\w+", re.UNICODE)
_NUM_ENTRY = re.compile(r"^(\d+)[.,](\d+)$")
_CYR_END = "аяоеыийьюу"
SHORT_LATIN = 4  # #1257: Latin words this short (SoC, PHY, Web, LTE) match whole words only, not as a prefix


def _norm(w: str) -> str:
    return w.lower().replace("ё", "е")


def _prefix_ok(stem: str) -> bool:
    """A stem may match longer words (routers, частоте) unless it is a short Latin word: "soc" must not fire on
    "social", "phy" on "physical", "web" on "website" (#1257)."""
    return not (len(stem) <= SHORT_LATIN and stem.isascii())


def _stem(w: str) -> str:
    w = _norm(w)
    if len(w) >= 5 and "а" <= w[0] <= "я" and w[-1] in _CYR_END:
        return w[:-1]  # частота -> частот (частоте, частоту, частоты)
    return w


@dataclass
class Entry:
    sources: list[str]
    target: str | None = None
    wrong: list[str] = field(default_factory=list)  # known wrong renderings of `target` (fixed after MT)
    asr_hint: bool = True  # False for the built-in list (#256): MT prompt / output only, never a Whisper hotword
    mt_hint: bool = True  # False for the default hotwords (#1397): Whisper hint only, never in the MT prompt
    whole_short: bool = False  # #1396: stems of <= SHORT_LATIN chars match whole words only (прод must not fire on продолжение)

    @property
    def main(self) -> str:
        return self.sources[0]

    @staticmethod
    def _short_ok(word: str, stem: str) -> bool:
        """#1396: a short stem (прод, лог) matches itself or itself plus one ending letter (проде, логи), never a
        longer word (продолжение, логика)."""
        return word == stem or (len(word) == len(stem) + 1 and word.startswith(stem))

    def matches(self, words: list[str]) -> bool:
        for src in self.sources:
            stems = [_stem(w) for w in _WORD.findall(src)]
            if not stems:
                continue
            for i in range(len(words) - len(stems) + 1):
                if all(self._short_ok(words[i + j], s) if self.whole_short and len(s) <= SHORT_LATIN else
                       words[i + j].startswith(s) if _prefix_ok(s) else words[i + j] in ((s, s + "s", s + "es") if s.isalpha() else (s,))
                       for j, s in enumerate(stems)):
                    return True
        return False


@dataclass
class Glossary:
    entries: list[Entry] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def hotwords(self, max_chars: int = HOTWORDS_MAX_CHARS) -> str | None:
        out, size = [], 0
        for e in self.entries:
            term = e.main
            if not e.asr_hint or _NUM_ENTRY.match(term):
                continue
            if size + len(term) + 2 > max_chars:
                break
            out.append(term)
            size += len(term) + 2
        return ", ".join(out) or None

    def relevant(self, *texts: str) -> list[Entry]:
        words = [_norm(w) for t in texts for w in _WORD.findall(t)]
        return [e for e in self.entries if e.mt_hint and e.matches(words)][:MAX_PROMPT_ENTRIES]

    def prompt_block(self, *texts: str) -> str:
        """Short instruction for the LLM, only with entries that occur in `texts`; '' if none."""
        rel = self.relevant(*texts)
        tr = [f"{e.main} = {e.target}" for e in rel if e.target]
        keep = [e.main for e in rel if not e.target]
        parts = []
        if tr:
            parts.append("Use these translations: " + "; ".join(tr) + ".")
        if keep:
            parts.append("Keep these terms as written: " + ", ".join(keep) + ".")
        return " ".join(parts)

    def fix_output(self, out: str, *texts: str, convert: Callable[[str], str] = lambda w: w) -> str:
        """Replace the known wrong renderings (`~` list) of every entry that occurs in `texts` (the source line
        and, for the OPUS pivot, its English intermediate) by the entry's target. `convert` maps glossary words
        to the output script (e.g. Traditional/Simplified) so both sides compare in the same script."""
        words = [_norm(w) for t in texts for w in _WORD.findall(t)]
        for e in self.entries:
            if not (e.target and e.wrong) or not e.matches(words):
                continue
            tgt = convert(e.target)
            for w in sorted(e.wrong, key=len, reverse=True):  # longest first: 猶豫不決 before 猶豫
                w = convert(w)
                if w and w != tgt and tgt not in w:
                    out = out.replace(w, tgt)
        return out

    def normalize_numbers(self, text: str) -> str:
        """"2 и 4" / "2,4" -> "2.4", only for numbers listed in the glossary."""
        for e in self.entries:
            m = _NUM_ENTRY.match(e.main)
            if not m:
                continue
            a, b = m.groups()
            text = re.sub(rf"(?<![\d.,]){a}(?:\s*,\s*|\s+(?:и|and|point|точка|dot)\s+){b}(?![\d])",
                          f"{a}.{b}", text, flags=re.I)
        return text


def parse(text: str) -> list[Entry]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            src, tgt = (s.strip() for s in line.split("=", 1))
            tgt, _, bad = (s.strip() for s in tgt.partition("~"))
            srcs = [s.strip() for s in src.split("|") if s.strip()]
            if srcs:
                out.append(Entry(srcs, tgt or None, [w.strip() for w in bad.split("|") if w.strip()]))
        else:
            srcs = [s.strip() for s in line.split("|") if s.strip()]
            if srcs:
                out.append(Entry(srcs))
    return out


def load(path) -> Glossary:
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("*.txt"))
        if not files:
            raise FileNotFoundError(f"glossary folder {p} has no .txt files")
    elif p.is_file():
        files = [p]
    else:
        raise FileNotFoundError(f"glossary path not found: {p}")
    entries: list[Entry] = []
    for f in files:
        entries.extend(parse(f.read_text(encoding="utf-8-sig")))
    return Glossary(entries)


BUILTIN_PATH = Path(__file__).parent / "data" / "tech-dev.txt"
BUILTIN_RU_PATH = Path(__file__).parent / "data" / "tech-ru.txt"


def load_builtin(lang: str | None = None) -> Glossary:
    """The built-in tech/dev list (#256). Its terms are not Whisper hotwords: biasing every talk towards
    "daemon, driver, kernel, ..." would make Whisper hear them where they were not said.
    #1396: with source language `lang` Russian (or unknown / "auto", the terms are Cyrillic and only fire on
    Russian words) the Russian software slang list is added; any other language gets the English list only."""
    g = load(BUILTIN_PATH)
    if not lang or lang == "auto" or lang.lower().split("-")[0] == "ru":
        ru = parse(BUILTIN_RU_PATH.read_text(encoding="utf-8-sig"))
        for e in ru:
            e.whole_short = True  # inflected forms of short terms are listed as extra spellings in the file
        g.entries.extend(ru)
    for e in g.entries:
        e.asr_hint = False
    return g


DEFAULT_HOTWORDS = {"ru": Path(__file__).parent / "data" / "hotwords-ru.txt"}  # #1397, per source language
# Languages whose default list is on without the env switch. Empty until the #1397 eval (scripts/bench/term_eval.py
# --set acronyms) shows better acronym accuracy with no extra false insertions and ru WER within 0.5 points.
DEFAULT_HOTWORDS_SHIPPED: frozenset[str] = frozenset()
DEFAULT_HOTWORDS_ENV = "TLWU_DEFAULT_HOTWORDS"  # "1": on for every language that has a list; "0": off


def default_hotwords_on(lang: str | None, env: dict | None = None) -> bool:
    """The default hotword list applies only to a fixed source language that has one (not "auto": the list would
    bias every other language towards Latin acronyms)."""
    import os

    code = (lang or "").lower().split("-")[0]
    if code not in DEFAULT_HOTWORDS:
        return False
    sw = (os.environ if env is None else env).get(DEFAULT_HOTWORDS_ENV, "")
    if sw in ("0", "1"):
        return sw == "1"
    return code in DEFAULT_HOTWORDS_SHIPPED


def load_default_hotwords(lang: str) -> Glossary:
    """The default hotwords of `lang` (#1397) as hint entries: Whisper hotwords only (mt_hint False)."""
    g = load(DEFAULT_HOTWORDS[lang.lower().split("-")[0]])
    for e in g.entries:
        e.asr_hint, e.mt_hint = True, False
    return g


def add_hotwords(g: Glossary | None, defaults: Glossary) -> Glossary:
    """Append default hotwords after the existing entries (#1397). An entry that already has the spelling wins: a
    user entry stays as it is; a built-in MT entry (asr_hint False) keeps its translation and becomes a hotword too."""
    entries = list(g.entries) if g else []
    have = {}
    for i, e in enumerate(entries):
        for s in e.sources:
            have.setdefault(_norm(s), i)
    for d in defaults.entries:
        hit = next((have[_norm(s)] for s in d.sources if _norm(s) in have), None)
        if hit is None:
            entries.append(d)
        elif not entries[hit].asr_hint:
            entries[hit] = replace(entries[hit], asr_hint=True)
    return Glossary(entries)


def with_default_hotwords(g: Glossary | None, lang: str | None, face_to_face=None, env: dict | None = None):
    """The CLI's glossary plus the default hotwords of the source language when they are on (#1397); face-to-face
    switches the source language per utterance, so it gets none."""
    if face_to_face or not default_hotwords_on(lang, env):
        return g
    return add_hotwords(g, load_default_hotwords(lang))


def merge(user: Glossary | None, base: Glossary) -> Glossary:
    """User entries first and win: a built-in entry sharing any source spelling with a user entry is dropped."""
    if not user:
        return Glossary(list(base.entries))
    taken = {_norm(s) for e in user.entries for s in e.sources}
    kept = [e for e in base.entries if not any(_norm(s) in taken for s in e.sources)]
    return Glossary(list(user.entries) + kept)
