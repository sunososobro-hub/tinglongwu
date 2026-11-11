"""Topic glossary: plain-text files (a file, or a folder of *.txt) that adapt tlwu to one topic.

Format (UTF-8, one entry per line):
    частота = 頻率           source = target translation
    чистота | частота = 頻率  `|` lists extra source spellings (common mis-hearings); first is the main one
    роутер                   no `=`: plain hint term (helps Whisper, kept as written by the LLM)
    fading = 衰落 ~ 淡化 | 消散  `~` lists known wrong renderings: when the entry occurs in the sentence (source
                             or English pivot), they are replaced by the target in the output (OPUS/M2M)
    # comment
Used two ways: source terms go to Whisper (`hotwords`); entries whose source occurs in the current sentence
or its context go into the Qwen prompt. Number entries (`2.4 = 2.4 GHz`) also normalize "2 и 4" -> "2.4".
Fully offline; reads local files only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HOTWORDS_MAX_CHARS = 200  # Whisper prompt budget: hotwords + previous text must stay far below 224 tokens
MAX_PROMPT_ENTRIES = 8
_WORD = re.compile(r"\w+", re.UNICODE)
_NUM_ENTRY = re.compile(r"^(\d+)[.,](\d+)$")
_CYR_END = "аяоеыийьюу"


def _norm(w: str) -> str:
    return w.lower().replace("ё", "е")


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

    @property
    def main(self) -> str:
        return self.sources[0]

    def matches(self, words: list[str]) -> bool:
        for src in self.sources:
            stems = [_stem(w) for w in _WORD.findall(src)]
            if not stems:
                continue
            for i in range(len(words) - len(stems) + 1):
                if all(words[i + j].startswith(s) for j, s in enumerate(stems)):
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
            if _NUM_ENTRY.match(term):
                continue
            if size + len(term) + 2 > max_chars:
                break
            out.append(term)
            size += len(term) + 2
        return ", ".join(out) or None

    def relevant(self, *texts: str) -> list[Entry]:
        words = [_norm(w) for t in texts for w in _WORD.findall(t)]
        return [e for e in self.entries if e.matches(words)][:MAX_PROMPT_ENTRIES]

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


def load_builtin() -> Glossary:
    return load(BUILTIN_PATH)


def merge(user: Glossary | None, base: Glossary) -> Glossary:
    """User entries first and win: a built-in entry sharing any source spelling with a user entry is dropped."""
    if not user:
        return Glossary(list(base.entries))
    taken = {_norm(s) for e in user.entries for s in e.sources}
    kept = [e for e in base.entries if not any(_norm(s) in taken for s in e.sources)]
    return Glossary(list(user.entries) + kept)
