"""Confirm-before-send controller for reverse mode (no GPU / audio hardware in here; everything is injected).

Flow per utterance: submit() -> synthesis starts in the background (serial, one worker) -> review loop shows
zh / ru / back-translation (set_back(), may arrive late) in submit order -> Enter sends the audio, "r" or "x" (+Enter) discards it.
Nothing reaches the output device before a confirmation. New utterances queue up while one waits.
synth(ru) may return one (audio, sr) or an iterable of (audio, sr) sentence parts: after Enter each part is sent as
soon as it is synthesized, so sentence 1 plays while the rest is still in TTS (#139).
"""
from __future__ import annotations

import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

DISCARD_KEYS = ("r", "x")
HELP = "[Enter]=send  [r]/[x]+Enter=discard"


@dataclass
class Utterance:
    n: int
    zh: str
    ru: str
    back: str = ""
    future: object = None
    decision: str = ""  # "sent" | "discarded" | "no_audio"
    extra: dict = field(default_factory=dict)
    lang: str = "ru"  # language of `ru` (the translated text): ru | en
    parts: object = None  # queue of synthesized (audio, sr) parts, None-terminated (filled by the TTS worker)


def setup_console() -> None:
    """Make Chinese/Russian print safely on a cp950 console (UTF-8 out, never raise on odd characters)."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:  # noqa: BLE001
            pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def back_translate(mt, ru: str, lang: str = "ru") -> str:
    """(ru|en) -> zh with the already loaded translator, leaving its zh->ru context (history/pair) untouched."""
    hist, pair = list(mt.history), getattr(mt, "_pair", None)
    try:
        return mt.translate(ru, lang, "zh").text
    except Exception as e:  # noqa: BLE001
        return f"(back-translation failed: {e})"
    finally:
        mt.history.clear()
        mt.history.extend(hist)
        mt._pair = pair


_END = None


def _fill(synth: Callable, ru: str, parts: queue.Queue) -> None:
    """TTS worker job: put every non-empty (audio, sr) part of synth(ru) on `parts` as it is produced, then _END."""
    try:
        r = synth(ru)
        for audio, sr in ([r] if isinstance(r, tuple) else (r or ())):
            if len(audio):
                parts.put((audio, sr))
    finally:
        parts.put(_END)


def _job_error(fut) -> BaseException | None:
    """The TTS job's exception (its end marker is queued just before the future completes, so wait briefly)."""
    from concurrent.futures import CancelledError
    from concurrent.futures import TimeoutError as FutTimeout

    try:
        return fut.exception(timeout=5)
    except (CancelledError, FutTimeout):
        return None


class ConfirmSession:
    """synth(ru) -> (audio, sr) | iterable of (audio, sr) | None ; send(audio, sr) ; preview(audio, sr) optional ; readline() -> str ('' = Enter)."""

    def __init__(self, synth: Callable, send: Callable, preview: Callable | None = None,
                 out: Callable = print, readline: Callable[[], str] | None = None,
                 set_voice: Callable[[int], None] | None = None):
        self.synth, self.send, self.preview, self.out = synth, send, preview, out
        self.set_voice = set_voice  # "v<N>"+Enter switches voice and re-synthesises the current utterance
        self.readline = readline or (lambda: input())
        self._q: queue.Queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=1)  # TTS pipe is serial
        self.items: list[Utterance] = []
        self._eof = False
        self._lock = threading.Lock()
        self._shown: set[int] = set()  # id() of utterances already printed (a late back-translation is appended)

    def _start(self, u: Utterance) -> None:
        u.parts = queue.Queue()
        u.future = self._pool.submit(_fill, self.synth, u.ru, u.parts)

    def submit(self, u: Utterance) -> None:
        self._start(u)  # synthesis starts now, in the background
        self.items.append(u)
        self._q.put(u)

    def set_back(self, u: Utterance, text: str) -> None:
        """Back-translation that arrives after submit() (#132: it must not delay synthesis). Printed with the
        utterance when it is shown later, or as its own line when the utterance is already on screen."""
        from .. import fmt

        with self._lock:
            u.back = text
            late = id(u) in self._shown
        if late and text:
            self.out(f"{fmt.INDENT}← BACK(zh): {text}")

    def finish(self) -> None:
        self._q.put(None)

    def abort(self) -> None:
        """Ctrl+C path: stop background synthesis without waiting."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _show(self, u: Utterance) -> None:
        from .. import fmt

        blk = fmt.block("me", u.zh, [(None, u.ru)], color=fmt.color_enabled(sys.stdout)).rstrip("\n")
        with self._lock:
            back = u.back
            self._shown.add(id(u))
        self.out(f"\n{blk}" + (f"\n{fmt.INDENT}← BACK(zh): {back}" if back else ""))

    def _ask(self, u: Utterance) -> bool:
        """True = send. EOF on input counts as discard."""
        pending = self._q.qsize()
        extra = f" ({pending} queued)" if pending else ""
        help_ = HELP + ("  [v0-v4]+Enter=change voice" if self.set_voice else "")
        while True:
            self.out(f"{help_}{extra}")
            try:
                key = self.readline().strip().lower()
            except EOFError:
                self._eof = True
                return False
            if self.set_voice and len(key) >= 2 and key[0] == "v" and key[1:].isdigit():
                self.set_voice(int(key[1:]))
                u.future.cancel()
                self._start(u)  # same text, new voice
                self.out(f"voice -> {key[1:]}")
                continue
            return key == ""  # only a bare Enter sends; r/x/anything else discards

    def _send_parts(self, u: Utterance) -> int:
        """Send (and preview) each part as soon as the worker has it; returns how many were sent."""
        n = 0
        while True:
            try:
                item = u.parts.get(timeout=0.25)  # timeout: Ctrl+C-able, and a cancelled job never ends the queue
            except queue.Empty:
                if u.future.done() and u.parts.empty():
                    return n
                continue
            if item is _END:
                return n
            audio, sr = item
            self.send(audio, sr)
            if self.preview:
                self.preview(audio, sr)
            n += 1

    def run(self) -> list[Utterance]:
        """Review loop; returns when finish() was called and the queue is drained."""
        while True:
            try:
                u = self._q.get(timeout=0.25)  # timeout: a bare blocking get() is not Ctrl+C-able on Windows
            except queue.Empty:
                continue
            if u is None:
                break
            self._show(u)
            if self._eof or not self._ask(u):
                u.future.cancel()
                u.decision = "discarded"
                self.out("discarded")
                continue
            n = self._send_parts(u)
            err = _job_error(u.future)
            if err is not None:
                self.out(f"TTS error: {err}")
            if not n:
                u.decision = "no_audio"
                self.out("no audio produced")
                continue
            u.decision = "sent"
            self.out("sent")
        self._pool.shutdown(wait=True)
        return self.items
