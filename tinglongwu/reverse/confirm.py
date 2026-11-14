"""Confirm-before-send controller for reverse mode (no GPU / audio hardware in here; everything is injected).

Flow per utterance: submit() -> synthesis starts in the background (serial, one worker) -> review loop shows
zh / ru / back-translation (set_back(), may arrive late) in submit order -> "y"+Enter sends the audio; a bare Enter,
"r", "x" or anything else discards it (#248: a stray Enter must never send). Keys typed ahead while the previous
utterance was playing are flushed before every prompt, so they cannot answer the next one.
Nothing reaches the output device before a confirmation. New utterances queue up while one waits.
synth(ru) may return one (audio, sr) or an iterable of (audio, sr) sentence parts: after "y" each part is sent as
soon as it is synthesized, so sentence 1 plays while the rest is still in TTS (#139).
"""
from __future__ import annotations

from .. import diag as _diag
from . import sendmode as _sm

import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

SEND_KEY = "y"  # the only answer that sends (case-insensitive); everything else, a bare Enter included, discards
DISCARD_KEYS = ("", "r", "x")
HELP = "[y]+Enter=send  [Enter]=discard"
# #249: bound the queue of sentences waiting for confirmation. More than MAX_PENDING unseen sentences means the
# speaker is far ahead of the review (the oldest is dropped); a sentence waiting longer than MAX_AGE_S is stale in a
# live conversation (dropped when its turn comes). Overrides: TLWU_CONFIRM_MAX_PENDING, TLWU_CONFIRM_MAX_AGE.
MAX_PENDING = 3
MAX_AGE_S = 30.0


def _env_num(name: str, default, cast=float):
    try:
        return cast(os.environ[name])
    except (KeyError, ValueError):
        return default


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
    t: float = 0.0  # clock() at submit (#249 expiry)


def setup_console() -> None:
    """Make Chinese/Russian print safely on a cp950 console (UTF-8 out, never raise on odd characters)."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass


def back_translate(mt, ru: str, lang: str = "ru", src: str = "zh") -> str:
    """(ru|en|..) -> `src` (zh; en when speaking English, #134) with the already loaded translator, leaving its
    forward context (history/pair) untouched."""
    hist, pair = list(mt.history), getattr(mt, "_pair", None)
    try:
        return mt.translate(ru, lang, src).text
    except Exception as e:  # noqa: BLE001
        return f"(back-translation failed: {e})"
    finally:
        mt.history.clear()
        mt.history.extend(hist)
        mt._pair = pair


def flush_input(stream=None) -> bool:
    """Drop keys typed ahead on the terminal (#248) so an Enter pressed while the previous utterance was playing
    cannot answer the next prompt. True when stdin is a TTY and was flushed; a non-TTY stdin (pipe, file,
    pytest capture) is left alone and never raises."""
    stream = sys.stdin if stream is None else stream
    try:
        fd = stream.fileno()
        if not stream.isatty():
            return False
    except (AttributeError, OSError, ValueError):
        return False
    try:
        if sys.platform == "win32":
            import msvcrt

            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios

            termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:  # noqa: BLE001
        return False
    return True


_END = None


def _fill(synth: Callable, ru: str, parts: queue.Queue, on_first: Callable[[], object] | None = None) -> None:
    """TTS worker job: put every non-empty (audio, sr) part of synth(ru) on `parts` as it is produced, then _END.
    on_first() runs once, just before the first part is queued (#207: first-audio latency)."""
    try:
        r = synth(ru)
        for audio, sr in ([r] if isinstance(r, tuple) else (r or ())):
            if len(audio):
                if on_first is not None:
                    on_first()
                    on_first = None
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
    """synth(ru) -> (audio, sr) | iterable of (audio, sr) | None ; send(audio, sr) ; preview(audio, sr) optional ;
    readline() -> str ('' = Enter) ; flush() drops type-ahead before each prompt (default: flush_input on stdin)."""

    def __init__(self, synth: Callable, send: Callable, preview: Callable | None = None,
                 out: Callable = print, readline: Callable[[], str] | None = None,
                 set_voice: Callable[[int], None] | None = None, flush: Callable[[], object] | None = None,
                 max_pending: int | None = None, max_age: float | None = None, clock: Callable[[], float] = time.monotonic,
                 mode: str = "confirm", countdown_s: float | None = None, require_back: bool = True,
                 sleep: Callable[[float], object] = time.sleep):
        self.synth, self.send, self.preview, self.out = synth, send, preview, out
        self.set_voice = set_voice  # "v<N>"+Enter switches voice and re-synthesises the current utterance
        self.readline = readline or (lambda: input())
        self.flush = flush or flush_input
        self._q: queue.Queue = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=1)  # TTS pipe is serial
        self.items: list[Utterance] = []
        self._eof = False
        self._lock = threading.Lock()
        self._shown: set[int] = set()  # id() of utterances already printed (a late back-translation is appended)
        self.max_pending = max(1, max_pending if max_pending is not None
                               else _env_num("TLWU_CONFIRM_MAX_PENDING", MAX_PENDING, int))
        self.max_age = max_age if max_age is not None else _env_num("TLWU_CONFIRM_MAX_AGE", MAX_AGE_S)
        self.clock = clock
        self.sleep = sleep
        self.mode = mode if mode in _sm.MODES else "confirm"  # #760
        self.countdown_s = countdown_s if countdown_s is not None else _sm._env_float("TLWU_SEND_COUNTDOWN", _sm.COUNTDOWN_S)
        self.require_back = require_back
        self._reader: tuple | None = None  # (thread, queue) of a readline still waiting after a countdown ended
        self._submit_lock = threading.Lock()
        self.on_first_part: Callable[[Utterance], object] | None = None  # #207: called once per utterance
        self.on_show: Callable[[Utterance], object] | None = None  # #494: called when a line is really shown

    def _first(self, u: Utterance) -> None:
        if self.on_first_part is not None and not u.extra.get("_first_seen"):
            u.extra["_first_seen"] = True  # a voice switch re-synthesizes: report the first time only
            self.on_first_part(u)

    def _start(self, u: Utterance) -> None:
        u.parts = queue.Queue()
        u.future = self._pool.submit(_fill, self.synth, u.ru, u.parts, lambda: self._first(u))

    def _drop(self, u: Utterance, reason: str) -> None:
        if u.future is not None:
            u.future.cancel()
        u.decision = "dropped"
        self.out(f"dropped ({reason}): {u.zh}")

    def submit(self, u: Utterance) -> None:
        u.t = self.clock()
        with self._submit_lock:
            while self._q.qsize() >= self.max_pending:  # #249: queue full -> drop the oldest waiting sentence
                try:
                    old = self._q.get_nowait()
                except queue.Empty:
                    break
                self._drop(old, "queue full")
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
        if self.on_show is not None:
            self.on_show(u)

    def _read_line(self) -> str:
        """readline(), or the line a countdown reader is still waiting on (so two readers never compete)."""
        if self._reader is None:
            return self.readline()
        q = self._reader[1]
        self._reader = None
        v = q.get()
        if v is None:
            raise EOFError
        return v

    def _countdown(self, u: Utterance) -> bool | None:
        """#760: show the countdown and wait. True = send ("y" or time ran out on a confident line), False = stopped
        (any other input) , None = time ran out but the line is low-confidence: fall back to the explicit prompt."""
        if self._reader is None:
            q: queue.Queue = queue.Queue()

            def read() -> None:
                try:
                    q.put(self.readline())
                except EOFError:
                    q.put(None)

            th = threading.Thread(target=read, daemon=True)
            self._reader = (th, q)
            th.start()
        q = self._reader[1]
        self.out(f"sending in {self.countdown_s:g} s  [any key+Enter]=stop  [y]+Enter=send now")
        end = self.clock() + self.countdown_s
        while True:
            try:
                key = q.get_nowait()
            except queue.Empty:
                key = False
            if key is not False:
                self._reader = None
                if key is None:
                    self._eof = True
                    return False
                return key.strip().lower() == SEND_KEY
            if self.clock() >= end:
                break
            self.sleep(0.05)
        why = _sm.low_confidence(u, self.require_back)
        if why:
            self.out(f"low confidence ({why}): waiting for confirm")
            return None
        return True

    def _ask(self, u: Utterance) -> bool:
        """True = send: only "y" (any case). A bare Enter, r/x, anything else or EOF discards."""
        if self.mode == "auto" and not _sm.low_confidence(u, self.require_back):
            return True
        if self.mode == "countdown":
            r = self._countdown(u)
            if r is not None:
                return r
        elif self.mode == "auto":
            self.out(f"low confidence ({_sm.low_confidence(u, self.require_back)}): waiting for confirm")
        pending = self._q.qsize()
        extra = f" ({pending} queued)" if pending else ""
        help_ = HELP + ("  [v0-v4]+Enter=change voice" if self.set_voice else "")
        while True:
            self.flush()  # #248: keys typed during the previous utterance must not answer this one
            self.out(f"{help_}{extra}")
            try:
                key = self._read_line().strip().lower()
            except EOFError:
                self._eof = True
                return False
            if self.set_voice and len(key) >= 2 and key[0] == "v" and key[1:].isdigit():
                self.set_voice(int(key[1:]))
                u.future.cancel()
                self._start(u)  # same text, new voice
                self.out(f"voice -> {key[1:]}")
                continue
            return key == SEND_KEY  # only "y" sends; a bare Enter, r/x and anything else discard

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
            if self.max_age and self.clock() - u.t > self.max_age:  # #249: waited too long, stale
                self._drop(u, f"expired after {self.max_age:g} s")
                continue
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
                why = getattr(self.synth, "no_audio_reason", None)  # #368: say why (worker error, no text, ...)
                self.out(f"no audio produced: {why(u.ru)}" if why else "no audio produced")
                continue
            u.decision = "sent"
            self.out("sent")
        self._pool.shutdown(wait=True)
        return self.items
