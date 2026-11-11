"""Live microphone input for reverse mode (no GPU needed here; capture/ASR are injected or lazily imported).

Reuses forward-mode capture (capture.mic.MicSource via soundcard) and Segmenter (Silero VAD pause splitting).
Feedback guard: the mic may never be the virtual-cable capture side ("CABLE Output") or the output device.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Iterable, Iterator

import numpy as np

POLL = 0.25
TAIL_S = 0.8  # keep the mic gated this long after our own speech ends (room echo / reverb)


class PlaybackGate:
    """Half-duplex gate: busy while our TTS is queued/playing, plus a short tail after it ends."""

    def __init__(self, tail: float = TAIL_S, clock=time.monotonic):
        self.tail, self.clock = tail, clock
        self._n, self._until = 0, 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self._n += 1

    def end(self) -> None:
        with self._lock:
            self._n = max(0, self._n - 1)
            self._until = self.clock() + self.tail

    def busy(self) -> bool:
        with self._lock:
            return self._n > 0 or self.clock() < self._until


def list_mic_names(include_loopback: bool = False) -> list[str]:
    import soundcard as sc

    return [m.name for m in sc.all_microphones(include_loopback=include_loopback)]


def is_virtual_mic(name: str) -> bool:
    """VB-CABLE capture side, BlackHole, our Linux virtual mic / monitors: never a person's microphone."""
    m = name.casefold()
    return "cable output" in m or "tlwu_" in m or "blackhole" in m or m.startswith("monitor of")


def resolve_mic_name(needle: str | None, names: list[str], default_name: str | None) -> str:
    """Name of the mic that will be used: first substring match, else the system default; when the default is a
    virtual cable (e.g. Windows default input set to "CABLE Output"), the first real microphone instead."""
    if needle:
        for n in names:
            if needle.casefold() in n.casefold():
                return n
        raise LookupError(f"no microphone matching {needle!r}; available: {names}")
    if default_name and is_virtual_mic(default_name):
        default_name = next((n for n in names if not is_virtual_mic(n)), default_name)
    if not default_name:
        raise LookupError("no default microphone found")
    return default_name


def check_mic_feedback(mic_name: str, out_device: str | None, out_name: str | None = None,
                       same_name_ok: bool = False, allow_monitor: bool = False) -> None:
    """Raise ValueError if the mic is (the capture side of) the output device or a virtual cable output.
    same_name_ok: Linux/PulseAudio names a card's mic and speaker identically and loopback monitors are
    listed separately ("Monitor of ..."), so a matching name there is the real mic, not a loopback.
    allow_monitor (--allow-monitor): "Monitor of <real card>" is accepted as input (play a clip, measure latency
    without speaking); our own tlwu_ sink/mic stays blocked, so there is no loop."""
    m = mic_name.casefold()
    bad = ("cable output" in m or "tlwu_" in m  # VB-CABLE capture side / Linux virtual mic (tlwu_mic, tlwu_sink.monitor)
           or "blackhole" in m  # macOS: BlackHole 2ch is system audio, 16ch is where our own speech goes
           or (m.startswith("monitor of") and not allow_monitor))
    for o in (() if same_name_ok else (out_device, out_name)):
        if not o:
            continue
        o = o.casefold()
        if o in m or m in o or o.replace("input", "output") in m or m.replace("output", "input") in o:
            bad = True
    if bad:
        raise ValueError(
            f"feedback guard: microphone {mic_name!r} is the output/virtual-cable device "
            f"(output={out_device or out_name!r}); pick your real microphone with --mic (see --list-mics)")


def capture_segments(source, segmenter, stop: threading.Event, lat=None) -> Iterator[np.ndarray]:
    """Capture + VAD segmentation in a background thread; yields finished utterances until `stop` is set.
    The capture thread never blocks on slow consumers (bounded queue absorbs bursts).
    `lat` (reverse.logic.Lat): the silence waited after each segment (the endpoint delay) is stored as 'vad' (#186)."""
    q: queue.Queue = queue.Queue(maxsize=32)
    done = object()

    def run():
        try:
            for seg in segmenter.segments(source.frames()):
                if stop.is_set():
                    return
                q.put((seg, getattr(segmenter, "last_pause", 0.0)))
            q.put(done)
        except BaseException as e:  # noqa: BLE001 - re-raised in the consumer
            q.put(e)

    threading.Thread(target=run, daemon=True).start()
    while not stop.is_set():
        try:
            item = q.get(timeout=POLL)
        except queue.Empty:
            continue
        if item is done:
            return
        if isinstance(item, BaseException):
            raise item
        seg, pause = item
        if lat is not None:
            lat.set("vad", float(pause))
        yield seg


def is_source_lang(text: str, src: str = "zh") -> bool:
    """Is `text` plausibly our speaker's language `src` (zh | en)? zh: False when it has Latin/Cyrillic/kana letters
    but no Chinese characters. en (#134): False when it has any CJK/kana or Cyrillic letter (the other side's
    Chinese leaked into the mic and Whisper(en) transcribed or translated it)."""
    import re

    from .logic import has_cjk

    if src == "en":
        return not has_cjk(text) and not re.search("[\u0400-\u04ff]", text)
    return has_cjk(text) or not re.search(r"[^\W\d_]", text)


def is_source_zh(text: str) -> bool:
    return is_source_lang(text, "zh")


def recognize_utterances(segments: Iterable[np.ndarray], asr, silence_rms: float = 1e-3,
                         gate: PlaybackGate | None = None, src: str = "zh") -> Iterator[str]:
    """Each finished segment -> text in `src` (zh | en) (silence, Whisper stock hallucinations and, with `gate`, segments
    heard while our own TTS is playing are dropped)."""
    from ..hallucination import is_hallucination

    for seg in segments:
        if gate is not None and gate.busy():
            continue
        rms = float(np.sqrt(np.mean(np.square(seg)))) if len(seg) else 0.0
        if rms < silence_rms:
            continue
        tr = asr.transcribe(seg)
        if tr is None or not tr.text.strip():
            continue
        if is_hallucination(tr.text, rms, getattr(tr, "no_speech", 0.0)):
            continue
        if not is_source_lang(tr.text, src):  # the other side's speech leaked in: Whisper "translated" it
            continue
        yield tr.text.strip()
