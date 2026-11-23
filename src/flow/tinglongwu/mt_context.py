"""#1750 --mt-context (revives #1394): give the translator the previous finished source sentence of the same stream.

Hy-MT2 sees one line at a time, so a line like "Удали старую ветку." (twig or branch?) is translated without the
line before it that says it is about a repository. With the switch on, the MT worker passes that previous line into
the model card's contextual prompt (`hymt.build_prompt(context=...)`). Rules:
  - source text only, never a translation, and at most one sentence (the last one of the previous line);
  - only a finished line counts: a unit the merger released at a cap / timeout (`forced`) is not a finished
    sentence, so it is not used and it clears the remembered line;
  - the same stream only: the same speaker label (None = one stream) and the same language (a line in another
    language replaces the memory, so after a language change there is no context);
  - never across a long pause: when this line starts CONTEXT_MAX_PAUSE_S or more after the previous line ended
    (audio clock), there is no context;
  - the context is only part of the prompt: it is never shown, saved or counted as part of the current line.
Off by default: eval-fragments numbers in docs/benchmarks.md ("Previous sentence as MT context, live", #1394)."""
from __future__ import annotations

import re

# A pause this long between two lines usually means a new turn or topic, where the previous line misleads more than
# it helps. Longer than the merger's pause-send (0.8 s, ja 0.5) so ordinary sentence gaps keep the context. #1750
# Goal ("not across a >= 4 s pause"), the value the #1394 wiring used; not tuned on data.
CONTEXT_MAX_PAUSE_S = 4.0

_SENT = re.compile(r"[^.!?。！？…]+[.!?。！？…]*")


def last_sentence(text: str) -> str:
    """The last sentence of `text` (the whole text when it has no sentence-final punctuation inside)."""
    parts = [p.strip() for p in _SENT.findall(text.strip()) if p.strip(" .!?。！？…")]
    return parts[-1] if parts else text.strip()


class LineContext:
    """Remembers the previous finished line per speaker. Used by the single MT worker thread only."""

    def __init__(self, max_pause: float = CONTEXT_MAX_PAUSE_S):
        self.max_pause = max_pause
        # who -> (lang, sentence, end on the audio clock, the entry it followed: for a #1395 replacement line)
        self._last: dict = {}

    def _ok(self, entry, lang: str, start: float | None) -> str | None:
        if entry is None or start is None:
            return None
        e_lang, text, end, _prev = entry
        if e_lang != lang or not text or end is None or start - end >= self.max_pause:
            return None
        return text

    def before(self, who, lang: str, start: float | None, replaces: bool = False) -> str | None:
        """Context for a line of `who` in `lang` that starts at `start` (audio seconds); None = translate alone.
        replaces (#1395): the line re-sends the previous line plus a late fragment, so its context is the line
        before the one it replaces."""
        entry = self._last.get(who)
        if replaces:
            entry = entry[3] if entry is not None else None
        return self._ok(entry, lang, start)

    def after(self, who, lang: str, text: str, end: float | None, finished: bool = True,
              replaces: bool = False) -> None:
        """Record the line just handled (translated, skipped or shown as is). An unfinished (forced) line clears it."""
        entry = self._last.get(who)
        prev = (entry[3] if entry is not None else None) if replaces else entry
        if not finished or not text.strip():
            self._last.pop(who, None)
            return
        self._last[who] = (lang, last_sentence(text), end, prev[:3] + (None,) if prev is not None else None)
