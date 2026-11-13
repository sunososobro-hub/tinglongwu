"""Test-only fake translator: src -> en -> tgt through injected step callables (the old OPUS PivotTranslator, #521).

Production no longer has a pivot translator; tests use this to drive the pipeline with canned translations."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from tinglongwu.translate import (Translation, Translator, protect_acronyms, restore_acronyms, zh_artifacts,
                                 zh_convert, zh_guard)


class PivotTranslator(Translator):
    """src -> en -> tgt (EN step skipped when either side is English)."""

    def __init__(self, models_dir: Path | None, zh_variant: str = "tw",
                 step_factory: Callable[[str, str], Callable[[str], str]] | None = None,
                 device: str = "cpu"):
        self.zh_variant = zh_variant
        self.device = device  # already resolved: "cpu" or "cuda"
        self._factory = step_factory or (lambda s, t: (_ for _ in ()).throw(AssertionError(f"no fake step for {s}->{t}")))
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
