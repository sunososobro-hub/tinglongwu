"""Live microphone input for reverse mode (no GPU needed here; capture/ASR are injected or lazily imported).

Reuses forward-mode capture (capture.mic.MicSource via soundcard) and Segmenter (Silero VAD pause splitting).
Feedback guard: the mic may never be the virtual-cable capture side ("CABLE Output") or the output device.
"""
from __future__ import annotations

import queue
import threading
from typing import Iterable, Iterator

import numpy as np

POLL = 0.25


def list_mic_names(include_loopback: bool = False) -> list[str]:
    import soundcard as sc

    return [m.name for m in sc.all_microphones(include_loopback=include_loopback)]


def resolve_mic_name(needle: str | None, names: list[str], default_name: str | None) -> str:
    """Name of the mic that will be used: first substring match, or the system default."""
    if needle:
        for n in names:
            if needle.casefold() in n.casefold():
                return n
        raise LookupError(f"no microphone matching {needle!r}; available: {names}")
    if not default_name:
        raise LookupError("no default microphone found")
    return default_name


def check_mic_feedback(mic_name: str, out_device: str | None, out_name: str | None = None) -> None:
    """Raise ValueError if the mic is (the capture side of) the output device or a virtual cable output."""
    m = mic_name.casefold()
    bad = "cable output" in m
    for o in (out_device, out_name):
        if not o:
            continue
        o = o.casefold()
        if o in m or m in o or o.replace("input", "output") in m or m.replace("output", "input") in o:
            bad = True
    if bad:
        raise ValueError(
            f"feedback guard: microphone {mic_name!r} is the output/virtual-cable device "
            f"(output={out_device or out_name!r}); pick your real microphone with --mic (see --list-mics)")


def capture_segments(source, segmenter, stop: threading.Event) -> Iterator[np.ndarray]:
    """Capture + VAD segmentation in a background thread; yields finished utterances until `stop` is set.
    The capture thread never blocks on slow consumers (bounded queue absorbs bursts)."""
    q: queue.Queue = queue.Queue(maxsize=32)
    done = object()

    def run():
        try:
            for seg in segmenter.segments(source.frames()):
                if stop.is_set():
                    return
                q.put(seg)
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
        yield item


def recognize_utterances(segments: Iterable[np.ndarray], asr, silence_rms: float = 1e-3) -> Iterator[str]:
    """Each finished segment -> Chinese text (silence and Whisper stock hallucinations dropped)."""
    from ..hallucination import is_hallucination

    for seg in segments:
        rms = float(np.sqrt(np.mean(np.square(seg)))) if len(seg) else 0.0
        if rms < silence_rms:
            continue
        tr = asr.transcribe(seg)
        if tr is None or not tr.text.strip():
            continue
        if is_hallucination(tr.text, rms, getattr(tr, "no_speech", 0.0)):
            continue
        yield tr.text.strip()
