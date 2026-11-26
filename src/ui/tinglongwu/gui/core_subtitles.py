"""#1917: subtitle rows split out of gui/core.py (the #3 hot file): the queue sinks that turn worker results into
GUI events, SubtitleState (what the overlay / scroll-back shows), and speaker_text (a bubble's speaker name).

No tkinter here either; gui.core re-exports every name, so `from tinglongwu.gui.core import SubtitleState` keeps working.
"""
from __future__ import annotations

import queue

from ..langs import lang_base
from ..sink import Result, Sink
from ..stream import DraftRenderer

SPEAK_MARK = "🎤 "  # #1001: a line of the speak leg in the shared subtitle list starts with this (my words)


class QueueSink(Sink):
    """Final lines -> queue (runs on the worker thread; never touches tkinter)."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        self.q.put(("source", original))  # shown dim until its "final" arrives (#53)

    prefix = ""  # OverlaySink: put before each translation on the bar

    def write_partial(self, r: Result) -> None:
        """#1771: the translation so far of a line still being translated; its "final" replaces it in place."""
        if r.src != r.tgt and r.target:
            self.q.put(("partial", r.original, self.prefix + r.target))

    def write(self, r: Result) -> None:
        from ..reverse.echoguard import shared

        if shared().check(r.original, r.target if r.src != r.tgt else ""):  # #1070: our own voice coming back
            self.q.put(("drop", r.original))  # #1532: its pending row (if shown) goes away instead of waiting forever
            return
        ev = ("final", r.original, r.target if r.src != r.tgt and r.target else r.original)
        if r.replaces is not None:  # #1395: the previous line + a late fragment, shown in that line's place
            ev = ("amend", r.replaces) + ev[1:]
        self.q.put(ev + (r.speaker,) if r.speaker else ev)  # 0.3.8: the async speaker token rides along


def wrap_bubble(text: str, measure, max_px: int) -> list[str]:
    """#1070: break `text` into lines that are at most max_px wide (measure(str) -> pixels): at spaces, and between
    CJK characters; a single over-long word is split by characters. Always at least one line."""
    import re

    tokens = re.findall(r"[぀-ヿ㐀-鿿가-힯]|[^\s぀-ヿ㐀-鿿가-힯]+|\s+", text.replace(chr(10), " "))
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if tok.isspace():
            cur += " " if cur else ""
            continue
        if cur and measure(cur + tok) > max_px:
            lines.append(cur.rstrip())
            cur = ""
        while measure(tok) > max_px and len(tok) > 1:  # one word wider than the bubble
            k = len(tok) - 1
            while k > 1 and measure(tok[:k]) > max_px:
                k -= 1
            lines.append(tok[:k])
            tok = tok[k:]
        cur += tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _zh_display(text: str, variant: str) -> str:
    """#1070: Whisper writes Chinese in Simplified; show it in the user's script. Falls back to the text as is."""
    try:
        from ..translate import zh_convert

        return zh_convert(text, variant)
    except Exception:  # noqa: BLE001 - opencc missing: show what Whisper wrote
        return text


class SpeakQueueSink(QueueSink):
    """#1001: the speak leg of a combined session: finished lines only ("final_side", marked with SPEAK_MARK), so
    they share the subtitle list with the listen leg without touching its draft / pending rows."""

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        pass

    def write_partial(self, r: Result) -> None:
        pass  # #1771: the speak leg shows finished lines only (it shares the list with the listen leg's rows)

    variant = "tw"  # #1070: the script my own words are shown in (zh-tw -> Traditional)

    def write(self, r: Result) -> None:
        out = r.target if r.src != r.tgt and r.target else r.original
        original = r.original
        if lang_base(r.src) == "zh":
            original = _zh_display(original, self.variant)
            if r.src == r.tgt or not r.target:
                out = original
        from ..reverse.echoguard import shared

        shared().note(r.original)  # #1070: the source of what we send, for the listen-leg echo guard
        self.q.put(("final_side", SPEAK_MARK + original, SPEAK_MARK + out))


class QueueDraftRenderer(DraftRenderer):
    """Drop-in for DraftRenderer that publishes draft events instead of writing ANSI to a terminal."""

    def __init__(self, q: queue.Queue):
        super().__init__(stream=None, columns=lambda: 100, enabled=True)
        self.q = q

    def clear(self) -> None:
        self.q.put(("clear",))

    def draw(self, src_tag, confirmed, tail, tgt_tag="", translation="", show_src=True) -> None:
        src = (confirmed + (" " + tail if confirmed and tail and " " in (confirmed + tail) else tail)).strip()
        if translation:
            self.q.put(("draft", src, translation))
        elif show_src:  # same-language / transcript-only: the source itself is the draft
            self.q.put(("draft", "", src))


def strip_silent_hint(text: str) -> str:
    """#1070/0.3.8: drop the "10 s without the other side's sound" line from a notice (any UI language); other
    notice lines stay. Called when listen-side audio or an other-side bubble arrives."""
    from ..i18n import MESSAGES

    heads = {m[k].split("{names}")[0] for m in MESSAGES.values() for k in ("listen_silent", "listen_silent_mic") if k in m}
    keep = [ln for ln in (text or "").split(chr(10)) if not any(ln.startswith(h) for h in heads)]
    return chr(10).join(keep).strip()


def speaker_text(book, label, fmt):
    """0.3.8: what a 對方 bubble's label shows: the user's name for that speaker, else "Speaker N" (fmt(n) in the UI
    language). None = no label. `book` is the session's speakers.SpeakerBook."""
    from ..speakers import LABEL_PREFIX

    shown = book.display(label) if label else None
    if not shown:
        return None
    if shown.startswith(LABEL_PREFIX) and shown[len(LABEL_PREFIX):].isdigit():
        return fmt(int(shown[len(LABEL_PREFIX):]))
    return shown


PENDING_MARK = " …"  # #1532: a heard line whose translation is still running: dim source text + this mark


class SubtitleState:
    """What the overlay shows: last `max_lines` lines; a draft is replaced in place by the final.

    #1532: a "source" event (ASR has the text, MT still running) adds the line at once, in its place, as a pending
    row (dim source + PENDING_MARK); its "final" fills that same row in place, so no other line moves and a
    translation that comes back out of order still lands on its own line."""

    def __init__(self, max_lines: int = 2):
        self.max_lines = max_lines
        # (src, target), oldest first; target None = pending (#1532: shown as dim source until its final arrives)
        self.finals: list[tuple[str, str | None]] = []
        self.draft: tuple[str, str] | None = None
        self.partial: dict[str, str] = {}  # #1771: source of a pending row -> its translation so far
        self.whos: list[str | None] = []  # 0.3.8: speaker token of each final (aligned with finals; None = none)
        self.paused = False  # frozen display: events still update the state (nothing is lost) but nothing is redrawn

    @property
    def pending(self) -> list[str]:
        """#53/#1532: sources heard whose translation is still running, oldest first."""
        return [src for src, tgt in self.finals if tgt is None]

    def apply(self, ev: tuple) -> bool:
        """Apply an event; True if the display should be redrawn."""
        if self._apply(ev):
            return not self.paused
        return False

    def _append(self, src: str, tgt: str | None, who: str | None) -> None:
        self.finals.append((src, tgt))
        self.whos.append(who)
        del self.finals[:-self.max_lines], self.whos[:-self.max_lines]

    def _apply(self, ev: tuple) -> bool:
        kind = ev[0]
        if kind == "draft":
            self.draft = (ev[1], ev[2])
        elif kind == "source":
            self._append(ev[1], None, None)
        elif kind == "partial":  # #1771 ("partial", src, text so far): fills the pending row (or the draft row)
            if any(tgt is None and src == ev[1] for src, tgt in self.finals):
                self.partial[ev[1]] = ev[2]
            else:  # no pending row (--stream drafts, or scrolled out): the draft row shows it until the final
                self.draft = (ev[1], ev[2])
        elif kind == "final":
            self.draft = None  # a live draft is replaced by its final
            self.partial.pop(ev[1], None)
            who = ev[3] if len(ev) > 3 else None
            i = next((k for k, (src, tgt) in enumerate(self.finals) if tgt is None and src == ev[1]), None)
            if i is None:  # never announced (or scrolled out): an ordinary new line
                self._append(ev[1], ev[2], who)
            else:  # #1532: the pending row becomes the final in place
                self.finals[i] = (ev[1], ev[2])
                self.whos[i] = who
        elif kind == "amend":  # #1395 ("amend", replaced src, src, target[, speaker]): newest final with that src
            if self.draft is not None and self.draft[0] in (ev[1], ev[2]):
                self.draft = None  # #1784: a stale streamed partial of this line must not outlive the amend
            i = next((k for k in range(len(self.finals) - 1, -1, -1)
                      if self.finals[k][0] == ev[1] and self.finals[k][1] is not None), None)
            if i is None:  # scrolled out (or never shown): an ordinary final line
                return self._apply(("final",) + ev[2:])
            self.finals[i] = (ev[2], ev[3])
            if len(ev) > 4:
                self.whos[i] = ev[4]
        elif kind == "drop":  # #1532: a heard line that will get no final (echo of our own voice): remove its row
            stale = self.draft is not None and self.draft[0] == ev[1]
            if stale:
                self.draft = None  # #1784: the dropped line's streamed partial goes away too
            i = next((k for k, (src, tgt) in enumerate(self.finals) if tgt is None and src == ev[1]), None)
            if i is None:
                return stale
            del self.finals[i], self.whos[i]
            self.partial.pop(ev[1], None)
        elif kind == "final_side":  # #1001: a speak-leg line next to a listen session: the draft row stays
            self._append(ev[1], ev[2], None)
        elif kind == "clear":
            if self.draft is None:
                return False
            self.draft = None
        else:
            return False
        return True

    def lines(self) -> list[tuple[str, bool]]:
        """[(target_text, is_draft)] oldest first, at most max_lines; a pending row is (source + mark, True)."""
        rows = [((self.partial.get(src) or src) + PENDING_MARK, True) if tgt is None else (tgt, False)
                for src, tgt in self.finals]
        if self.draft:
            rows.append((self.draft[1], True))
        return rows[-self.max_lines:]

    def history_who(self) -> list[str | None]:
        """0.3.8: speaker token per history() row (None for the draft and for lines without one)."""
        return list(self.whos) + ([None] if self.draft else [])

    def source(self) -> str:
        """Source text of the newest shown line."""
        if self.draft:
            return self.draft[0]
        return self.finals[-1][0] if self.finals else ""

    def history(self) -> list[tuple[str, str, bool]]:
        """[(src, target_text, is_draft)] for the scroll-back: every kept line, then the draft (if any).
        A pending row (#1532) is ("", source + mark, True): its text is the source, so no second source line."""
        rows = [((src, self.partial[src] + PENDING_MARK, True) if src in self.partial else ("", src + PENDING_MARK, True))
                if tgt is None else (src, tgt, False) for src, tgt in self.finals]
        if self.draft:
            rows.append((self.draft[0], self.draft[1], True))
        return rows

    def last_text(self) -> str:
        rows = self.lines()
        return rows[-1][0] if rows else ""
