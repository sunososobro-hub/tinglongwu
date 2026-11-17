"""#1394 --mt-context: give the translator the previous finished source sentence of the same stream as context.

Hy-MT2 sees one line at a time, so a line like "Удали старую ветку." (twig or branch?) is translated without the
line before it that says it is about a repository. With the setting on, the MT worker passes that previous line
into the model card's contextual prompt (`hymt.build_prompt(context=...)`). Rules:
  - source text only, never a translation, and at most one sentence (the last one of the previous line);
  - only a finished line counts (a unit the merger cut at a timeout, `forced`, is not a finished sentence: it is
    not used and it clears the remembered line);
  - the same stream only: the same speaker label (None = one stream) and the same detected language;
  - never across a long pause: when this line starts more than CONTEXT_MAX_PAUSE_S after the previous line
    ended (audio clock), there is no context;
  - the context is only part of the prompt: it is never shown, saved or counted as part of the current line.
Off by default (eval-fragments numbers in docs/benchmarks.md)."""
from __future__ import annotations

import re

# A pause this long between two lines usually means a new turn or topic, where the previous line misleads more
# than it helps. Longer than the merger's pause-send (0.8 s, ja 0.5) so ordinary sentence gaps keep the context.
CONTEXT_MAX_PAUSE_S = 4.0

_SENT = re.compile(r"[^.!?。！？…]+[.!?。！？…]*")


def last_sentence(text: str) -> str:
    """The last sentence of `text` (the whole text when it has no sentence-final punctuation inside)."""
    parts = [p.strip() for p in _SENT.findall(text.strip()) if p.strip(" .!?。！？…")]
    return parts[-1] if parts else text.strip()


class LineContext:
    """Remembers the previous finished line per (speaker, language). Used by the single MT worker thread only."""

    def __init__(self, max_pause: float = CONTEXT_MAX_PAUSE_S):
        self.max_pause = max_pause
        self._last: dict = {}  # (who, lang) -> (text, end on the audio clock)

    def before(self, who, lang: str, start: float | None) -> str | None:
        """Context for a line of `who` in `lang` that starts at `start` (audio seconds); None = translate alone."""
        prev = self._last.get((who, lang))
        if prev is None or start is None:
            return None
        text, end = prev
        if end is None or start - end > self.max_pause:
            return None
        return text

    def after(self, who, lang: str, text: str, end: float | None, finished: bool = True) -> None:
        """Record the line just handled (translated or not). An unfinished (forced) line clears the memory."""
        key = (who, lang)
        if not finished or not text.strip():
            self._last.pop(key, None)
            return
        self._last[key] = (last_sentence(text), end)
