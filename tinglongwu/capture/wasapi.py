"""Windows listen capture (WASAPI loopback).

#1070: Meet / Zoom play to whatever output the user picked in the meeting ("headphones"), which is often NOT the
Windows default ("speakers"), so a default-only loopback heard nothing. With no --capture-device we now listen to
EVERY active output at once and mix them; our own voice device (VB-CABLE Input, BlackHole, tlwu_sink) is never
listened to (that is our own TTS coming back). A device that appears / disappears, or a changed default, is
followed because the list is refreshed every few seconds.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from collections.abc import Iterator

import numpy as np

from ._soundcard import BLOCK, NATIVE_RATE, SoundcardSource, _recorder, com_initialized, find_by_name, \
    patch_mixformat, suppress_capture_warnings
from .base import to_mono_16k

OWN_OUTPUTS = ("cable", "vb-audio", "blackhole", "tlwu_sink")  # our TTS goes here: listening = echo
SILENT_AFTER_S = 10.0
SILENT_PEAK = 5e-4  # digital silence is exactly 0; quiet speech is far above this
REFRESH_S = 2.0


def is_own_output(name: str | None) -> bool:
    """True for a virtual cable / our own sink: never a listen source."""
    n = (name or "").casefold()
    return any(k in n for k in OWN_OUTPUTS)


def listen_names(names: list[str]) -> list[str]:
    """The outputs worth listening to: everything but our own cables."""
    return [n for n in names if n and not is_own_output(n)]


class SilenceWatch:
    """update(peak, now) -> True when the listened sound has been silent for `after` seconds, and again every
    `after` seconds while it stays silent (a hint a bubble cleared meanwhile comes back); sound re-arms it."""

    def __init__(self, after: float = SILENT_AFTER_S, clock=time.monotonic):
        self.after, self.clock = after, clock
        self._since: float | None = None
        self._fired = False

    def update(self, peak: float, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        if peak >= SILENT_PEAK:
            self._since, self._fired = None, False
            return False
        if self._since is None:
            self._since = now
        if now - self._since >= self.after:
            self._fired = True
            self._since = now  # still silent: say it again after another `after` s
            return True
        return False


def mix(blocks: list[np.ndarray]) -> np.ndarray:
    """Sum equal-shaped float blocks (shorter ones are zero padded), clipped to [-1, 1]."""
    n = max(len(b) for b in blocks)
    out = np.zeros((n,) + blocks[0].shape[1:], dtype=np.float32)
    for b in blocks:
        out[: len(b)] += b
    return np.clip(out, -1.0, 1.0)


class WasapiLoopbackSource(SoundcardSource):
    """Windows: WASAPI loopback of every real output (default), or of one output (--capture-device substring)."""

    def __init__(self, device: str | None = None):
        super().__init__(device)
        self.on_sound = None  # called once when sound returns after on_silent fired
        self.on_silent = None  # called with the list of listened device names after ~10 s of silence
        self.listening: list[str] = [device] if device else []

    def _pick_microphone(self, sc):
        if self.device:
            want = self.device.casefold()
            for sp in sc.all_speakers():  # by speaker id: a headset mic can share the speaker name
                if want in sp.name.casefold():
                    if is_own_output(sp.name):
                        raise RuntimeError(f"{sp.name!r} is our own voice output; it cannot be listened to")
                    return sc.get_microphone(sp.id, include_loopback=True)
            return find_by_name(sc, self.device)
        return sc.get_microphone(sc.default_speaker().id, include_loopback=True)

    def frames(self) -> Iterator[np.ndarray]:
        watch = SilenceWatch()
        gen = super().frames() if self.device else self._auto_frames()
        for blk in gen:
            was_fired = watch._fired
            if self.on_silent is not None and watch.update(float(np.abs(blk).max()) if len(blk) else 0.0):
                try:
                    self.on_silent(list(self.listening))
                except Exception:  # noqa: BLE001 - a hint must never stop the capture
                    pass
            elif was_fired and not watch._fired and self.on_sound is not None:
                try:
                    self.on_sound()
                except Exception:  # noqa: BLE001
                    pass
            yield blk

    # -- all outputs, mixed -------------------------------------------------------------------
    def _worker(self, sc, spk_id: str, q: queue.Queue, stop: threading.Event) -> None:
        try:
            with com_initialized():
                mic = sc.get_microphone(spk_id, include_loopback=True)
                with _recorder(mic) as rec:
                    while not (stop.is_set() or self._stop.is_set()):
                        data = rec.record(numframes=BLOCK)
                        try:
                            q.put_nowait(data)
                        except queue.Full:
                            try:
                                q.get_nowait()
                            except queue.Empty:
                                pass
                            q.put_nowait(data)
        except Exception as e:  # noqa: BLE001 - device gone: the refresh loop re-opens it if it is still listed
            if not (stop.is_set() or self._stop.is_set()):
                print(f"tlwu: listen device closed ({(str(e) or type(e).__name__).splitlines()[0][:100]})",
                      file=sys.stderr, flush=True)
        finally:
            stop.set()

    def _auto_frames(self) -> Iterator[np.ndarray]:
        import soundcard as sc

        patch_mixformat()
        suppress_capture_warnings()
        workers: dict[str, tuple[queue.Queue, threading.Event, str]] = {}
        last_refresh = -REFRESH_S
        try:
            with com_initialized():
                while not self._stop.is_set():
                    now = time.monotonic()
                    if now - last_refresh >= REFRESH_S:
                        last_refresh = now
                        try:
                            want = {sp.id: sp.name for sp in sc.all_speakers() if not is_own_output(sp.name)}
                        except Exception:  # noqa: BLE001 - enumeration hiccup: keep what we have
                            want = {k: v[2] for k, v in workers.items()}
                        for k in [k for k, v in workers.items() if k not in want or v[1].is_set()]:
                            workers.pop(k)[1].set()
                        for k, name in want.items():
                            if k not in workers:
                                q, ev = queue.Queue(maxsize=20), threading.Event()
                                workers[k] = (q, ev, name)
                                threading.Thread(target=self._worker, args=(sc, k, q, ev), daemon=True).start()
                        self.listening = [v[2] for v in workers.values()]
                    blocks = []
                    for q, _ev, _n in list(workers.values()):
                        try:
                            blocks.append(q.get(timeout=0.15 if not blocks else 0.02))
                        except queue.Empty:
                            pass
                    if not blocks:
                        if not workers:
                            self._stop.wait(0.1)
                        blocks = [np.zeros((BLOCK, 2), dtype=np.float32)]
                    yield to_mono_16k(mix(blocks), NATIVE_RATE)
        finally:
            for _q, ev, _n in workers.values():
                ev.set()
