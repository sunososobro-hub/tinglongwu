"""Confirm-before-send controller for reverse mode (no GPU / audio hardware in here; everything is injected).

Flow per utterance: submit() -> synthesis starts in the background (serial, one worker) -> review loop shows
zh / ru / back-translation in submit order -> Enter sends the audio, "r" or "x" (+Enter) discards it.
Nothing reaches the output device before a confirmation. New utterances queue up while one waits.
"""
from __future__ import annotations

import queue
import sys
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


def back_translate(mt, ru: str) -> str:
    """ru -> zh with the already loaded translator, leaving its zh->ru context (history/pair) untouched."""
    hist, pair = list(mt.history), getattr(mt, "_pair", None)
    try:
        return mt.translate(ru, "ru", "zh").text
    except Exception as e:  # noqa: BLE001
        return f"(back-translation failed: {e})"
    finally:
        mt.history.clear()
        mt.history.extend(hist)
        mt._pair = pair


class ConfirmSession:
    """synth(ru) -> (audio, sr) | None ; send(audio, sr) ; preview(audio, sr) optional ; readline() -> str ('' = Enter)."""

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

    def submit(self, u: Utterance) -> None:
        u.future = self._pool.submit(self.synth, u.ru)  # starts now, in the background
        self.items.append(u)
        self._q.put(u)

    def finish(self) -> None:
        self._q.put(None)

    def abort(self) -> None:
        """Ctrl+C path: stop background synthesis without waiting."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _show(self, u: Utterance) -> None:
        self.out(f"\n--- #{u.n} ---\nZH : {u.zh}\nRU : {u.ru}\nBACK(zh): {u.back}")

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
                u.future = self._pool.submit(self.synth, u.ru)  # same text, new voice
                self.out(f"voice -> {key[1:]}")
                continue
            return key == ""  # only a bare Enter sends; r/x/anything else discards

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
            try:
                res = u.future.result()
            except Exception as e:  # noqa: BLE001
                res = None
                self.out(f"TTS error: {e}")
            if not res or len(res[0]) == 0:
                u.decision = "no_audio"
                self.out("no audio produced")
                continue
            audio, sr = res
            self.send(audio, sr)
            if self.preview:
                self.preview(audio, sr)
            u.decision = "sent"
            self.out("sent")
        self._pool.shutdown(wait=True)
        return self.items
