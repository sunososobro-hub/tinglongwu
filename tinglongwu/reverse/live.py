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
    `lat` (reverse.logic.Lat): the silence waited after each segment (the endpoint delay) is stored as 'vad' (#186),
    and lat.marks gets "eos" (end of speech = arrival of the newest frame minus that silence) and "cut" (the time
    the segment was cut), on lat's clock (#434 latency records)."""
    q: queue.Queue = queue.Queue(maxsize=32)
    done = object()
    clock = getattr(lat, "clock", None) or time.perf_counter
    frame_t = [0.0]

    def stamped(frames):
        for f in frames:
            frame_t[0] = clock()
            yield f

    ended = threading.Event()  # the consumer is gone: the capture thread must not block on a full queue

    def put(item) -> None:
        while not ended.is_set():
            try:
                q.put(item, timeout=POLL)
                return
            except queue.Full:
                continue

    def run():
        try:
            for seg in segmenter.segments(stamped(source.frames()) if lat is not None else source.frames()):
                if stop.is_set() or ended.is_set():
                    return
                pause = float(getattr(segmenter, "last_pause", 0.0))
                put((seg, pause, (frame_t[0] - pause, clock()) if lat is not None else None))
            put(done)
        except BaseException as e:  # noqa: BLE001 - re-raised in the consumer
            put(e)

    th = threading.Thread(target=run, name="tlwu-speak-capture", daemon=True)
    th.start()
    try:
        while not stop.is_set():
            try:
                item = q.get(timeout=POLL)
            except queue.Empty:
                continue
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            seg, pause, marks = item
            if lat is not None:
                lat.set("vad", pause)
                lat.marks = {"eos": marks[0], "cut": marks[1]}
            yield seg
    finally:
        stop_capture(source, th, ended)


CAPTURE_JOIN_S = 3.0  # a soundcard recorder notices close() within one 100 ms block (capture/_soundcard.py)


def stop_capture(source, th: threading.Thread, ended: threading.Event | None = None,
                 timeout: float = CAPTURE_JOIN_S) -> bool:
    """#427: close the audio source and wait for its capture thread. Without this the daemon thread stays inside
    the recorder (PulseAudio calls) after Ctrl+C and the interpreter shuts down under it: Linux live speak mode
    then hung after 'peak VRAM' or died with SIGABRT. Returns True when the thread has ended."""
    if ended is not None:
        ended.set()
    close = getattr(source, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass
    if th is not threading.current_thread():
        th.join(timeout)
    return not th.is_alive()


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


def recognize_utterances(segments: Iterable[np.ndarray], asr, silence_rms: float | None = None,
                         gate: PlaybackGate | None = None, src: str = "zh", sr: int = 16000,
                         report=None, zh_variant: str | None = None) -> Iterator[str]:
    """Each finished segment -> text in `src` (zh | en). Dropped: segments heard while our own TTS plays (`gate`),
    anything the speak-side input gate rejects (#249: too short, quiet, no_speech / low logprob, too few characters,
    compression loop, Whisper repeat hallucinations) and the other side's speech leaking in. Gate drops are counted
    in `report` (gate.DropReport: one summary line per minute at most). silence_rms overrides gate.MIN_RMS.
    zh_variant (tw | cn, src zh only; #312): the text goes through the same zh_convert as listen mode, so the
    source line is Traditional (tw) or Simplified (cn) whatever script Whisper wrote."""
    from .gate import DropReport, SegmentStats, gate_audio
    from .gate import gate as input_gate

    report = report or DropReport()
    over = {} if silence_rms is None else {"min_rms": silence_rms}
    for seg in segments:
        if gate is not None and gate.busy():
            continue
        rms = float(np.sqrt(np.mean(np.square(seg)))) if len(seg) else 0.0
        dur = len(seg) / sr
        ok, why = gate_audio(dur, rms, **over)
        if not ok:
            if why != "quiet":  # silence between phrases is normal, not worth a count
                report.add(why)
            continue
        tr = asr.transcribe(seg)
        if tr is None or not tr.text.strip():
            continue
        ok, why = input_gate(SegmentStats(dur, rms, tr.text, float(getattr(tr, "no_speech", 0.0) or 0.0),
                                          float(getattr(tr, "avg_logprob", 0.0) or 0.0)), **over)
        if not ok:
            report.add(why)
            continue
        if not is_source_lang(tr.text, src):  # the other side's speech leaked in: Whisper "translated" it
            continue
        text = tr.text.strip()
        if src == "zh" and zh_variant:
            from ..translate import zh_convert

            text = zh_convert(text, zh_variant)
        yield text
