"""Streaming drafts (Google-Live-Caption style): while a VAD segment is still running, re-transcribe the
growing buffer about once a second and show a dim DRAFT (source + quick translation) that the final line
replaces. Concept: LocalAgreement from the whisper_streaming paper (a prefix is confirmed once two
consecutive hypotheses agree); no code taken from that project."""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import threading
import time
import unicodedata

import numpy as np

from .capture.base import SAMPLE_RATE, AudioSource
from . import fmt  # noqa: F401  (registers the label_me/label_them strings)
from .hallucination import is_hallucination
from .i18n import t
from .langfollow import script_lang
from .translate import translate_checked

DRAFT_INTERVAL = 0.6  # min seconds between partial passes (ASR and MT alternate on one thread)
TRANSLATE_MIN_CHARS = 8  # re-translate when the partial grew by >= this many chars ...
TRANSLATE_MIN_GAP = 1.0  # ... or when >= this many seconds passed since the last draft translation
SILENCE_RMS = 1e-3
DIM, RESET = "\x1b[2m", "\x1b[0m"
CLEAR_DOWN = "\x1b[J"
ELLIPSIS = "..."
CJK_LANGS = {"zh", "ja", "ko"}
MASK_K = 2  # #273 mask-k: the last k not-yet-confirmed source tokens are left out of the draft translation


def mask_tail(conf: str, tail: str, cjk: bool, k: int = MASK_K) -> str:
    """Source text for a draft translation: the confirmed prefix plus the unconfirmed tail without its last k
    tokens (they change most between partials and make the translation flicker). Nothing confirmed and the whole
    tail masked: the full text, so the first draft is not delayed."""
    toks = tokens(tail, cjk)
    kept = toks[:max(0, len(toks) - k)]
    if not conf and not kept:
        kept = toks
    return join_tokens([x for x in (conf, join_tokens(kept, cjk)) if x], cjk)


def enable_vt() -> bool:
    """Turn on ANSI escape processing in the Windows console (no-op elsewhere)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            os.system("")  # fallback trick: makes conhost enable VT
            return True
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001
        os.system("")
        return True


def is_cjk_text(text: str, lang: str | None = None) -> bool:
    if lang in CJK_LANGS:
        return True
    letters = [c for c in text if not c.isspace()]
    n = sum("぀" <= c <= "鿿" or "가" <= c <= "힯" for c in letters)
    return bool(letters) and n * 2 > len(letters)


def tokens(text: str, cjk: bool) -> list[str]:
    """Words, or single characters for CJK (no spaces). Whitespace is not significant."""
    return [c for c in text if not c.isspace()] if cjk else text.split()


def join_tokens(toks: list[str], cjk: bool) -> str:
    return "".join(toks) if cjk else " ".join(toks)


class LocalAgreement:
    """Confirmed prefix = longest common prefix of two consecutive hypotheses (grows monotonically unless
    a later hypothesis contradicts it, then it shrinks to what still agrees)."""

    def __init__(self):
        self.prev: list[str] = []
        self.confirmed: list[str] = []

    def update(self, text: str, cjk: bool = False) -> tuple[str, str]:
        """Feed a new hypothesis; returns (confirmed_text, unconfirmed_tail_text)."""
        cur = tokens(text, cjk)
        n = 0
        for a, b in zip(self.prev, cur):
            if a != b:
                break
            n += 1
        c = self.confirmed
        self.confirmed = cur[:max(n, len(c))] if cur[:len(c)] == c else cur[:n]
        self.prev = cur
        return join_tokens(self.confirmed, cjk), join_tokens(cur[len(self.confirmed):], cjk)


def display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def fit_tail(s: str, width: int) -> tuple[str, int]:
    """Keep the end of s that fits in `width` columns. Returns (text, chars_cut_from_front)."""
    if display_width(s) <= width:
        return s, 0
    kept, w = [], len(ELLIPSIS)
    for c in reversed(s):
        cw = display_width(c)
        if w + cw > width:
            break
        kept.append(c)
        w += cw
    return ELLIPSIS + "".join(reversed(kept)), len(s) - len(kept)


class DraftRenderer:
    """Overwrites a small block (source line + translation line) in place; final lines print normally.
    Lines are truncated to the terminal width so one draft line is exactly one row."""

    def __init__(self, stream=None, columns=None, enabled: bool = True):
        self.stream = stream or sys.stdout
        self.columns = columns or (lambda: shutil.get_terminal_size((100, 24)).columns)
        self.enabled = enabled
        self.rows = 0  # rows drawn; the cursor sits at the end of the last one
        self.lock = threading.RLock()

    def _erase(self) -> str:
        if not self.rows:
            return ""
        up = f"\x1b[{self.rows - 1}A" if self.rows > 1 else ""
        return f"{up}\r{CLEAR_DOWN}"

    def clear(self) -> None:
        with self.lock:
            if self.enabled and self.rows:
                self.stream.write(self._erase())
                self.stream.flush()
                self.rows = 0

    def draw(self, src_tag: str, confirmed: str, tail: str, tgt_tag: str = "", translation: str = "",
             show_src: bool = True) -> None:
        if not self.enabled:
            return
        width = max(20, self.columns() - 1)
        label = t("label_them")  # same ◀ Them / → labels as the final lines (fmt.block); draft is the listen side
        head = f"{label} [{src_tag}] "
        tail_s = (" " + tail if confirmed and tail and " " in (confirmed + tail) else tail)
        full = confirmed + tail_s
        text, cut = fit_tail(full, width - display_width(head))
        pre = ELLIPSIS if cut else ""
        conf_vis = confirmed[cut:]
        tail_vis = tail_s[max(0, cut - len(confirmed)):]
        lines = [f"{head}{pre}{conf_vis}{DIM}{tail_vis}{RESET}"] if show_src else []
        if translation:
            tgt_head = f"→ [{tgt_tag}] "
            tr, _ = fit_tail(translation, width - display_width(tgt_head))
            lines.append(f"{DIM}{tgt_head}{tr}{RESET}")
        if not lines:
            return  # translation-only mode and nothing translated yet: show nothing
        with self.lock:
            self.stream.write(self._erase() + "\n".join(lines))
            self.stream.flush()
            self.rows = len(lines)


class DraftEngine:
    """Core of --stream, thread-free so it is testable. One worker thread alternates tick() (Whisper partial
    of the live buffer) and translate_pending() (Qwen/OPUS draft of the WHOLE current partial hypothesis), so
    neither starves the other and the Chinese appears right after each partial. The confirmed prefix
    (LocalAgreement) only drives the source line's bright/dim split; translation re-runs when the partial grew
    >= TRANSLATE_MIN_CHARS or >= TRANSLATE_MIN_GAP s passed. By default only the target-language line is shown."""

    def __init__(self, asr, translator, renderer: DraftRenderer, live, tgt: str,
                 interval: float = DRAFT_INTERVAL, clock=time.monotonic, show_src: bool = False,
                 min_chars: int = TRANSLATE_MIN_CHARS, min_gap: float = TRANSLATE_MIN_GAP, log=None,
                 mask_k: int = MASK_K):
        self.asr, self.translator, self.renderer, self.live, self.tgt = asr, translator, renderer, live, tgt
        self.interval, self.clock, self.show_src = interval, clock, show_src
        self.min_chars, self.min_gap, self.log = min_chars, min_gap, log  # log(str) = --debug-latency sink
        self.lock = threading.Lock()  # serialises model use: the final path blocks, drafts skip when busy
        self.final_waiting = 0  # finals queued on the lock: drafts step aside so they cannot starve a final
        self._fw_lock = threading.Lock()
        self.agree = LocalAgreement()
        self.epoch: int | None = None
        self.shown: tuple | None = None
        self.translation = ""
        self.dropped = 0  # stale translation requests discarded
        self.starts: dict[int, float] = {}  # epoch -> estimated segment audio start (clock time)
        self.first_zh: list[float] = []  # seconds from segment audio start to first translated draft shown
        self.finals: list[float] = []  # seconds from segment audio start to final line printed
        self._first_done: set[int] = set()
        self._seen: tuple = (None, 0)
        self._next = 0.0
        self._slot: tuple | None = None  # newest request; a newer one replaces (drops) an older one
        self._slot_lock = threading.Lock()
        self._translated = ""  # hypothesis text last translated
        self._t_translated = -1e9
        self.mask_k = mask_k
        # held() -> (text, lang): ASR text the sentence merger still holds (set by pipeline.run). The draft translates
        # it together with the live partial, so it stays on screen until the whole-sentence final replaces it (#273)
        self.held = None
        self.n_drafts = 0  # draft translations shown
        self.rewrites = 0  # ... of which changed characters already shown (not a pure extension): flicker

    def current_epoch(self) -> int | None:
        cur = self.live()
        return cur[0] if cur else None

    @contextlib.contextmanager
    def final_section(self):
        """Final ASR/MT path: wait for the models, and make drafts yield meanwhile."""
        with self._fw_lock:
            self.final_waiting += 1
        try:
            with self.lock:
                yield
        finally:
            with self._fw_lock:
                self.final_waiting -= 1

    def can_translate(self, lang: str) -> bool:
        return self.translator is not None and lang != self.tgt

    def tick(self) -> bool:
        """One partial pass if due and there is new audio. True if a Whisper partial ran."""
        now = self.clock()
        if now < self._next or self.final_waiting:
            return False
        cur = self.live()
        if not cur or (cur[0], len(cur[1])) == self._seen:
            return False
        epoch, audio = cur
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < SILENCE_RMS:
            return False
        if not self.lock.acquire(blocking=False):
            return False  # final ASR/MT is busy: skip, never delay it
        try:
            self._next = now + self.interval
            self._seen = (epoch, len(audio))
            if epoch != self.epoch:
                self.epoch, self.agree, self.translation, self._translated, self.shown = epoch, LocalAgreement(), "", "", None
                self._t_translated = -1e9
            self.starts.setdefault(epoch, now - len(audio) / SAMPLE_RATE)
            t = self.asr.partial(np.asarray(audio))
        finally:
            self.lock.release()
        if t is not None and is_hallucination(t.text, rms, t.no_speech):
            t = None  # stock subtitle-credit line on music/silence: never draft it
        if t is None or not t.text.strip() or self.current_epoch() != epoch:
            return True  # nothing heard, or the segment ended meanwhile (the final line owns the display)
        t.lang = script_lang(t.text, t.lang)  # kana labelled zh is Japanese (#86)
        cjk = is_cjk_text(t.text, t.lang)
        conf, tail = self.agree.update(t.text, cjk)
        text = self._with_held(mask_tail(conf, tail, cjk, self.mask_k), t.lang, cjk)
        if self.can_translate(t.lang) and text != self._translated and self._due(text):
            with self._slot_lock:
                if self._slot is not None:
                    self.dropped += 1
                self._slot = (epoch, text, t.lang)
        self._render((epoch, conf, tail, t.lang))
        return True

    def _with_held(self, text: str, lang: str, cjk: bool) -> str:
        """Prefix the merger's held text (same language), minus the words the new segment repeats (VAD overlap)."""
        held = self.held() if self.held is not None else None
        if not held or not held[0] or held[1] != lang:
            return text
        from .merge import dedupe_overlap

        rest = dedupe_overlap(held[0].split()[-10:], text)
        return held[0] + ("" if cjk else " ") + rest if rest else held[0]

    def flicker_line(self) -> str:
        """--debug-latency summary: average rewrites of already-shown draft characters per draft (#273)."""
        n = self.n_drafts
        return f"draft rewrites {self.rewrites / n if n else 0:.2f} per draft ({n} drafts, mask-k {self.mask_k})"

    def _due(self, text: str) -> bool:
        if not self.translation:
            return True  # nothing shown yet: translate immediately
        grown = abs(len(text) - len(self._translated)) >= self.min_chars
        return grown or self.clock() - self._t_translated >= self.min_gap

    def _render(self, shown: tuple) -> None:
        epoch, conf, tail, lang = shown
        with self.renderer.lock:
            if self.current_epoch() != epoch:
                return
            self.shown = shown
            if self.translation and epoch not in self._first_done and epoch in self.starts:
                self._first_done.add(epoch)
                dt = self.clock() - self.starts[epoch]
                self.first_zh.append(dt)
                self._log(f"first {self.tgt} draft epoch={epoch} {dt:.2f}s after segment audio start")
            self.renderer.draw(lang.upper(), conf, tail, self.tgt.upper(), self.translation,
                               show_src=self.show_src or not self.can_translate(lang))

    def _log(self, msg: str) -> None:
        if self.log:
            with self.renderer.lock:
                self.renderer.clear()  # the draft block must not share a row with the stderr line
                self.log("latency: " + msg)

    def translate_pending(self) -> bool:
        """Translate the newest queued hypothesis (one at a time). True if a request was taken."""
        if self.final_waiting:
            return False
        with self._slot_lock:
            req, self._slot = self._slot, None
        if req is None:
            return False
        epoch, text, lang = req
        if self.current_epoch() != epoch:
            self.dropped += 1
            return True
        if not self.lock.acquire(blocking=False):
            with self._slot_lock:  # final is busy: put it back (unless a newer one arrived), try later
                if self._slot is None:
                    self._slot = req
            return False
        try:
            if self.current_epoch() != epoch:
                self.dropped += 1
                return True
            try:
                tr = translate_checked(self.translator, text, lang, self.tgt, draft=True)
            except Exception:  # noqa: BLE001 - a failed draft must never break the pipeline
                return True
            if tr is None:
                return True  # untranslated Japanese left in the draft (#86): keep the previous draft line
            out = tr.text
        finally:
            self.lock.release()
        if self.current_epoch() != epoch or epoch != self.epoch:
            self.dropped += 1
            return True
        self.n_drafts += 1
        if self.translation and not out.startswith(self.translation):
            self.rewrites += 1
        self._translated, self.translation, self._t_translated = text, out, self.clock()
        if self.shown and self.shown[0] == epoch:
            self._render(self.shown)
        return True

    def final_written(self, epoch: int | None = None) -> None:
        """Call right before a final line is printed: wipe the draft block; log latency of segment `epoch`."""
        self.renderer.clear()
        self.shown = None
        if epoch is not None:
            t0 = None
            for e in [e for e in self.starts if e <= epoch]:
                v = self.starts.pop(e)
                t0 = v if t0 is None else min(t0, v)
                self._first_done.discard(e)
            if t0 is not None:
                dt = self.clock() - t0
                self.finals.append(dt)
                self._log(f"final epoch={epoch} {dt:.2f}s after segment audio start")

    def start(self, stop: threading.Event) -> None:
        def loop():  # ASR partial then MT draft, alternating: no starvation either way
            while not stop.is_set():
                try:
                    a = self.tick()
                    b = self.translate_pending()
                    if not (a or b):
                        stop.wait(0.05)
                except Exception:  # noqa: BLE001
                    stop.wait(0.5)

        threading.Thread(target=loop, daemon=True).start()


class PacedSource(AudioSource):
    """Wraps a file source and releases frames in real time (so --stream --file behaves like live audio)."""

    def __init__(self, inner: AudioSource, clock=time.monotonic, sleep=time.sleep):
        self.inner, self.clock, self.sleep = inner, clock, sleep

    def frames(self):
        t0, played = self.clock(), 0.0
        for f in self.inner.frames():
            played += len(f) / SAMPLE_RATE
            while (wait := t0 + played - self.clock()) > 0:
                self.sleep(min(wait, 0.25))  # short slices keep Ctrl+C responsive
            yield f

    def close(self) -> None:
        self.inner.close()
