from __future__ import annotations

import contextlib
import queue
import threading
import time

import numpy as np

from .asr import Recognizer
from .hallucination import is_hallucination
from .capture.base import SAMPLE_RATE, AudioSource
from .merge import SentenceMerger
from .repeat import collapse_repeats
from .segment import Segmenter
from .sink import Result, Sink
from .translate import Translator

SILENCE_RMS = 1e-3
_DONE = object()
# Ctrl+C: on Windows a blocking Queue.get() with no timeout is not interruptible, so the main thread
# always waits in short slices and KeyboardInterrupt is delivered within ~POLL seconds.
POLL = 0.25


def run(source: AudioSource, segmenter: Segmenter, asr: Recognizer,
        translator: Translator | None, sink: Sink, tgt: str = "zh",
        merger: SentenceMerger | None = None, audio_clock: bool = False, draft=None,
        stop_event: threading.Event | None = None) -> None:
    """Capture -> segments -> ASR -> (sentence merge) -> translate -> sink.

    With a merger, ASR text is held until a sentence ends, or max_wait seconds passed (a timer wakes
    the loop even when no new segment arrives, so live latency stays bounded). audio_clock=True uses
    the amount of audio consumed as the clock (offline files run faster than real time).
    """
    q: queue.Queue = queue.Queue(maxsize=8)
    consumed = [0]
    stop = threading.Event()

    def counted(frames):
        for f in frames:
            consumed[0] += len(f)
            yield f

    def produce():
        try:
            for seg in segmenter.segments(counted(source.frames())):
                if stop.is_set():
                    return
                q.put((seg, consumed[0] / SAMPLE_RATE, getattr(segmenter, "epoch", 1) - 1,
                       bool(getattr(segmenter, "last_forced", False)), float(getattr(segmenter, "last_pause", 0.0))))
            q.put(_DONE)
        except BaseException as e:  # noqa: BLE001 - re-raised in the consumer
            q.put(e)

    # --stream: drafts share the models with this path; `model_lock` makes the final path win (drafts skip when busy)
    model_lock = draft.final_section if draft is not None else contextlib.nullcontext

    last_ep = [None]  # epoch of the newest segment handed to ASR (for --debug-latency)

    def write(r: Result) -> None:
        if draft is not None:
            draft.final_written(last_ep[0])  # the final line replaces the dim draft
        sink.write(r)

    def emit(unit_text: str, lang: str) -> None:
        unit_text = collapse_repeats(unit_text)  # merged segments can loop across the seam
        if translator is None:  # transcript-only
            write(Result(lang, unit_text, None, "", tgt))
            return
        if lang == tgt:  # e.g. Chinese heard while translating to zh: show as is (Traditional via OpenCC)
            if tgt == "zh":
                from .translate import zh_convert

                unit_text = zh_convert(unit_text, getattr(translator, "zh_variant", "tw"))
            write(Result(lang, unit_text, None, unit_text, tgt))
            return
        with model_lock():
            tr = translator.translate(unit_text, lang, tgt)
        write(Result(lang, unit_text, tr.pivot, tr.text, tgt))

    def emit_units(units) -> None:
        for u in units:
            emit(u.text, u.lang)

    threading.Thread(target=produce, daemon=True).start()
    if draft is not None:
        draft.start(stop)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break  # GUI Stop button
            timeout = POLL
            has_deadline = merger is not None and merger.deadline is not None and not audio_clock
            if has_deadline:
                timeout = min(POLL, max(0.0, merger.deadline - time.monotonic()))
            try:
                item = q.get(timeout=timeout)
            except queue.Empty:
                if has_deadline:
                    emit_units(merger.due(time.monotonic()))
                continue
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                raise item
            seg, stamp, last_ep[0], forced_cut, pause = item
            if float(np.sqrt(np.mean(seg ** 2))) < SILENCE_RMS:
                continue  # silence: skip (avoids Whisper hallucinations)
            with model_lock():
                t = asr.transcribe(seg)
            if t is None or is_hallucination(t.text, float(np.sqrt(np.mean(seg ** 2))), t.no_speech):
                continue  # nothing heard, or a stock Whisper subtitle-credit line on music/silence
            if merger is None or translator is None:
                emit(t.text, t.lang)
                continue
            emit_units(merger.add(t.text, t.lang, stamp if audio_clock else time.monotonic(),
                                   forced_cut=forced_cut, pause=pause))
        if merger is not None and translator is not None:
            emit_units(merger.flush())
    finally:
        stop.set()
        if draft is not None:
            draft.renderer.clear()
        source.close()
