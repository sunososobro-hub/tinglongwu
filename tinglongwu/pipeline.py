from __future__ import annotations

import contextlib
import queue
import sys
import threading
import time

import numpy as np

from .asr import Recognizer
from .hallucination import is_hallucination
from .i18n import t as _msg
from .capture.base import SAMPLE_RATE, AudioSource
from .langfollow import script_lang
from .merge import SentenceMerger, Unit
from .models import ModelNotFound
from .repeat import collapse_repeats
from .segment import Segmenter
from .sink import Result, Sink
from .translate import Translation, Translator, translate_checked

SILENCE_RMS = 1e-3
_DONE = object()
# Ctrl+C: on Windows a blocking Queue.get() with no timeout is not interruptible, so the main thread
# always waits in short slices and KeyboardInterrupt is delivered within ~POLL seconds.
POLL = 0.25


def step_times(translator) -> str:
    """' (ru-en 410 ms, en-zh 530 ms)' from the OPUS pivot's last call, '' for other engines."""
    for t in (translator, getattr(translator, "base", None)):
        steps = getattr(t, "last_timing", None)
        if steps:
            return " (" + ", ".join(f"{k} {v * 1000:.0f} ms" for k, v in steps) + ")"
    return ""


def run(source: AudioSource, segmenter: Segmenter, asr: Recognizer,
        translator: Translator | None, sink: Sink, tgt: str = "zh",
        merger: SentenceMerger | None = None, audio_clock: bool = False, draft=None,
        stop_event: threading.Event | None = None, log=None, on_latency=None, speakers=None) -> None:
    """Capture -> segments -> ASR -> (sentence merge) -> translate -> sink.

    Translation and output run in their own worker thread (one, so lines stay in order): ASR of the next
    segment never waits for MT of the previous one; on a slow CPU lines no longer arrive in bursts behind
    a backlog. log(str): --debug-latency, per-stage times (asr / queue wait / each MT step).
    on_latency(seconds): called once per output line with ASR time + MT queue wait + MT time (#80).

    With a merger, ASR text is held until a sentence ends, or max_wait seconds passed (a timer wakes
    the loop even when no new segment arrives, so live latency stays bounded). audio_clock=True uses
    the amount of audio consumed as the clock (offline files run faster than real time).
    """
    q: queue.Queue = queue.Queue(maxsize=8)
    consumed = [0]
    stop = threading.Event()

    def counted(frames):
        for f in frames:
            if stop.is_set():  # Stop pressed: end the capture generator even if no segment is ever emitted
                return
            consumed[0] += len(f)
            yield f

    def produce():
        try:
            frames = source.frames()
            try:
                for seg in segmenter.segments(counted(frames)):
                    if stop.is_set():
                        return
                    if not put((seg, consumed[0] / SAMPLE_RATE, getattr(segmenter, "epoch", 1) - 1,
                           bool(getattr(segmenter, "last_forced", False)), float(getattr(segmenter, "last_pause", 0.0)))):
                        return
            finally:
                # #10: end the capture generator here, on the thread that opened the recorder (owns its COM
                # scope on Windows), so the device is released now. Relying on refcounting is not enough: an
                # error's traceback pins the suspended generator until the cyclic GC runs (mic stays in use).
                close = getattr(frames, "close", None)
                if close is not None:
                    close()
            put(_DONE)
        except BaseException as e:  # noqa: BLE001 - re-raised in the consumer
            put(e)

    def put(item) -> bool:
        """q.put that gives up once Stop is set (a full queue must not pin the producer thread)."""
        while not stop.is_set():
            try:
                q.put(item, timeout=0.2)
                return True
            except queue.Full:
                pass
        return False

    # --stream: drafts share the models with this path; `model_lock` makes the final path win (drafts skip when busy)
    model_lock = draft.final_section if draft is not None else contextlib.nullcontext

    last_ep = [None]  # epoch of the newest segment handed to ASR (for --debug-latency)
    last_asr = [0.0]  # seconds the newest segment spent in ASR
    say = log or (lambda m: None)
    if log is not None and draft is not None and getattr(draft, "log", None):
        say = lambda m: draft._log(m.removeprefix("latency: "))  # noqa: E731 - clears the draft block first

    def write(r: Result, ep) -> None:
        if draft is not None:
            draft.final_written(ep)  # the final line replaces the dim draft
        sink.write(r)

    def emit(unit_text: str, lang: str, ep, queued: float) -> None:
        if translator is None:  # transcript-only
            write(Result(lang, unit_text, None, "", tgt), ep)
            return
        if lang == tgt:  # e.g. Chinese heard while translating to zh: show as is (Traditional via OpenCC)
            if tgt == "zh":
                from .translate import zh_convert

                unit_text = zh_convert(unit_text, getattr(translator, "zh_variant", "tw"))
            write(Result(lang, unit_text, None, unit_text, tgt), ep)
            return
        with model_lock():
            t0 = time.monotonic()
            try:
                tr = translate_checked(translator, unit_text, lang, tgt)
            except ModelNotFound as e:  # a language turned up whose model is not installed: say so, keep running
                if (lang, tgt) not in missing:
                    missing.add((lang, tgt))
                    print(f"tlwu: {e}", file=sys.stderr)
                tr = Translation(None, _msg("mt_pair_missing", src=lang, tgt=tgt))
            if tr is None:  # the engine echoed the source twice
                tr = Translation(None, _msg("mt_untranslated"))
            dt = time.monotonic() - t0
            steps = step_times(translator)
        say(f"latency: mt {dt * 1000:.0f} ms{steps} (waited {(t0 - queued) * 1000:.0f} ms in queue)")
        write(Result(lang, unit_text, tr.pivot, tr.text, tgt), ep)

    missing: set = set()  # (src, tgt) pairs already reported as not installed
    mtq: queue.Queue = queue.Queue()  # units waiting for MT + output; unbounded (short text)
    failed: list[BaseException] = []

    def mt_worker() -> None:
        while True:
            try:
                item = mtq.get(timeout=POLL)
            except queue.Empty:
                if stop.is_set():
                    return
                continue
            if item is _DONE or stop.is_set():
                return
            try:
                emit(*item[:4])
                if on_latency is not None:
                    on_latency(item[4] + time.monotonic() - item[3])
            except BaseException as e:  # noqa: BLE001 - re-raised in the main thread
                failed.append(e)
                return

    def emit_units(units) -> None:
        for u in units:
            text = collapse_repeats(u.text)  # merged segments can loop across the seam
            lang = script_lang(text, u.lang)  # kana labelled zh is Japanese: translate it, never pass it through (#86)
            if translator is not None and draft is None and lang != tgt and hasattr(sink, "write_source"):
                sink.write_source(lang, text)  # #53: the source line now, its translation when MT is done
            mtq.put((text, lang, last_ep[0], time.monotonic(), last_asr[0]))

    def check_worker() -> None:
        if failed:
            raise failed[0]

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    worker = threading.Thread(target=mt_worker, daemon=True)
    worker.start()
    if draft is not None:
        draft.start(stop)
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break  # GUI Stop button
            check_worker()
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
                t0 = time.monotonic()
                t = asr.transcribe(seg)
            last_asr[0] = time.monotonic() - t0
            say(f"latency: asr {last_asr[0] * 1000:.0f} ms for {len(seg) / SAMPLE_RATE:.1f} s of audio"
                f" ({mtq.qsize()} line(s) waiting for MT)")
            if t is None or is_hallucination(t.text, float(np.sqrt(np.mean(seg ** 2))), t.no_speech):
                continue  # nothing heard, or a stock Whisper subtitle-credit line on music/silence
            if speakers is not None:  # --speakers (#222): label this sentence, log only; output lines unchanged
                who, spk = speakers.label(seg)
                print(f"tlwu: {who}", file=sys.stderr, flush=True)
                say(f"latency: spk {spk:.3f} s ({who})")
            if merger is None or translator is None:
                emit_units([Unit(t.text, t.lang, False)])
                continue
            emit_units(merger.add(t.text, t.lang, stamp if audio_clock else time.monotonic(),
                                   forced_cut=forced_cut, pause=pause))
        if merger is not None and translator is not None:
            emit_units(merger.flush())
        mtq.put(_DONE)
        while worker.is_alive():  # finish the queued lines (short waits: Ctrl+C stays responsive)
            if stop_event is not None and stop_event.is_set():
                break
            worker.join(timeout=POLL)
        check_worker()
    finally:
        stop.set()
        mtq.put(_DONE)
        if draft is not None:
            draft.renderer.clear()
        source.close()  # releases the capture device (mic indicator off)
        producer.join(timeout=1.0)
