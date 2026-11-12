from __future__ import annotations

import contextlib
import queue
import re
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
from .repeat import filter_hallucination
from .segment import Segmenter
from .sink import Result, Sink
from .translate import Translation, Translator, translate_checked

SILENCE_RMS = 1e-3
_DONE = object()
# Ctrl+C: on Windows a blocking Queue.get() with no timeout is not interruptible, so the main thread
# always waits in short slices and KeyboardInterrupt is delivered within ~POLL seconds.
POLL = 0.25
DUP_WINDOW = 10.0  # seconds: an identical final line this soon after the same one is dropped (Whisper repeats on silence)


def _dup_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower().replace("ё", "е"))


def step_times(translator) -> str:
    """' (ru-en 410 ms, en-zh 530 ms)' from the OPUS pivot's last call, '' for other engines."""
    for t in (translator, getattr(translator, "base", None)):
        steps = getattr(t, "last_timing", None)
        if steps:
            return " (" + ", ".join(f"{k} {v * 1000:.0f} ms" for k, v in steps) + ")"
    return ""


def _record(item, t_shown: float) -> dict:
    """#323: one latency record for a final line from its MT queue item and the time it was shown."""
    text, lang, _ep, released = item[:4]
    eos, cut, asr_done = item[6]
    r = lambda v: round(v, 3)  # noqa: E731
    return {"vad": r(cut - eos), "asr": r(asr_done - cut), "merge": r(released - asr_done),
            "mt": r(t_shown - released), "total": r(t_shown - eos),
            "t_source_shown": None if item[7] is None else r(item[7] - eos), "lang": lang, "text": text}


def sink_shows_source(sink) -> bool:
    """#323: True if `sink` displays source lines (overrides write_source); a wrapper sink may say so itself via a
    `shows_source` attribute (#330 VoiceSink forwards its inner sink's answer)."""
    own = getattr(sink, "shows_source", None)
    if own is not None:
        return bool(own)
    return getattr(type(sink), "write_source", Sink.write_source) is not Sink.write_source


def run(source: AudioSource, segmenter: Segmenter, asr: Recognizer,
        translator: Translator | None, sink: Sink, tgt: str = "zh",
        merger: SentenceMerger | None = None, audio_clock: bool = False, draft=None,
        stop_event: threading.Event | None = None, log=None, on_latency=None, speakers=None,
        on_work=None, on_record=None) -> None:
    """Capture -> segments -> ASR -> (sentence merge) -> translate -> sink.

    Translation and output run in their own worker thread (one, so lines stay in order): ASR of the next
    segment never waits for MT of the previous one; on a slow CPU lines no longer arrive in bursts behind
    a backlog. log(str): --debug-latency, per-stage times (asr / queue wait / each MT step).
    on_latency(seconds): called once per output line with ASR time + MT queue wait + MT time (#80).
    on_work(asr_s, mt_s, seg_s): once per output line, pure compute (no queue wait) and the audio length (#280).
    on_record(dict) (#323, live clock only): once per final line, seconds (3 decimals) from the end of speech:
    vad (end of speech -> segment cut), asr (cut -> ASR done, incl. waiting for the ASR thread), merge (held for a
    sentence end), mt (MT queue + translate + output), total (end of speech -> final line shown; the sum of the four),
    t_source_shown (end of speech -> source line handed to a sink that shows it, None otherwise), plus lang and
    text. A unit held by the merger keeps the marks of the newest segment whose text it holds. End of speech = the
    arrival time of the last speech sample the VAD saw (last frame arrival minus the silence after the speech).

    With a merger, ASR text is held until a sentence ends, or max_wait seconds passed (a timer wakes
    the loop even when no new segment arrives, so live latency stays bounded). audio_clock=True uses
    the amount of audio consumed as the clock (offline files run faster than real time).
    """
    q: queue.Queue = queue.Queue(maxsize=8)
    consumed = [0]
    frame_t = [0.0]  # monotonic arrival time of the newest frame (#323)
    stop = threading.Event()

    def counted(frames):
        for f in frames:
            if stop.is_set():  # Stop pressed: end the capture generator even if no segment is ever emitted
                return
            consumed[0] += len(f)
            frame_t[0] = time.monotonic()
            yield f

    def produce():
        try:
            frames = source.frames()
            try:
                for seg in segmenter.segments(counted(frames)):
                    if stop.is_set():
                        return
                    pause = float(getattr(segmenter, "last_pause", 0.0))
                    if not put((seg, consumed[0] / SAMPLE_RATE, getattr(segmenter, "epoch", 1) - 1,
                                bool(getattr(segmenter, "last_forced", False)), pause,
                                (frame_t[0] - pause, time.monotonic()))):  # (end of speech, cut) for on_record
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
    last_len = [0.0]  # seconds of audio in the newest segment
    last_marks = [None]  # (end of speech, cut, ASR done) of the newest transcribed segment (#323 on_record)
    # #323: a sink that does not override write_source shows no source line, so it gets no t_source_shown
    shows_source = sink_shows_source(sink)
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
                t_emit = time.monotonic()
                emit(*item[:4])
                if on_record is not None and item[6] is not None:
                    on_record(_record(item, time.monotonic()))
                if on_work is not None:
                    on_work(item[4], time.monotonic() - t_emit, item[5])
                if on_latency is not None:
                    on_latency(item[4] + time.monotonic() - item[3])
            except BaseException as e:  # noqa: BLE001 - re-raised in the main thread
                failed.append(e)
                return

    last_final = ["", -1e9]  # normalized text and clock of the last emitted final line

    def emit_units(units) -> None:
        for u in units:
            text = filter_hallucination(u.text)  # merged segments can loop across the seam
            if not text:
                continue
            key, now = _dup_key(text), consumed[0] / SAMPLE_RATE if audio_clock else time.monotonic()
            if key and key == last_final[0] and now - last_final[1] <= DUP_WINDOW:
                continue  # identical final line again within the window: show it once
            last_final[0], last_final[1] = key, now
            lang = script_lang(text, u.lang)  # kana labelled zh is Japanese: translate it, never pass it through (#86)
            t_src = None
            if translator is not None and draft is None and lang != tgt and hasattr(sink, "write_source"):
                sink.write_source(lang, text)  # #53: the source line now, its translation when MT is done
                t_src = time.monotonic() if shows_source else None
            marks = u.tag if u.tag is not None else last_marks[0]  # the unit's own segment, not the newest one
            mtq.put((text, lang, last_ep[0], time.monotonic(), last_asr[0], last_len[0], marks, t_src))

    def check_worker() -> None:
        if failed:
            raise failed[0]

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    worker = threading.Thread(target=mt_worker, daemon=True)
    worker.start()
    if draft is not None:
        if merger is not None and hasattr(draft, "held"):  # #273 re-translation: drafts show, finals wait for a
            merger.whole = True  # whole sentence (a timeout cuts at a clause boundary, never mid-clause)
            draft.held = lambda: (merger.buf, merger.lang)
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
            seg, stamp, last_ep[0], forced_cut, pause, (t_eos, t_cut) = item
            if float(np.sqrt(np.mean(seg ** 2))) < SILENCE_RMS:
                continue  # silence: skip (avoids Whisper hallucinations)
            with model_lock():
                t0 = time.monotonic()
                t = asr.transcribe(seg)
            last_asr[0] = time.monotonic() - t0
            marks = (t_eos, t_cut, t0 + last_asr[0])
            last_len[0] = len(seg) / SAMPLE_RATE
            say(f"latency: asr {last_asr[0] * 1000:.0f} ms for {len(seg) / SAMPLE_RATE:.1f} s of audio"
                f" ({mtq.qsize()} line(s) waiting for MT)")
            if t is None or is_hallucination(t.text, float(np.sqrt(np.mean(seg ** 2))), t.no_speech):
                continue  # nothing heard, or a stock Whisper subtitle-credit line on music/silence
            last_marks[0] = marks
            if speakers is not None:  # --speakers (#222): label this sentence, log only; output lines unchanged
                who, spk = speakers.label(seg)
                print(f"tlwu: {who}", file=sys.stderr, flush=True)
                say(f"latency: spk {spk:.3f} s ({who})")
            if merger is None or translator is None:
                emit_units([Unit(t.text, t.lang, False, marks)])
                continue
            emit_units(merger.add(t.text, script_lang(t.text, t.lang), stamp if audio_clock else time.monotonic(),
                                   forced_cut=forced_cut, pause=pause, tag=marks))
        if merger is not None and translator is not None:
            emit_units(merger.flush())
        mtq.put(_DONE)
        while worker.is_alive():  # finish the queued lines (short waits: Ctrl+C stays responsive)
            if stop_event is not None and stop_event.is_set():
                break
            worker.join(timeout=POLL)
        check_worker()
        if draft is not None and hasattr(draft, "flicker_line"):  # after the MT worker drained: stays the last line
            say("latency: " + draft.flicker_line())
    finally:
        stop.set()
        mtq.put(_DONE)
        if draft is not None:
            draft.renderer.clear()
        source.close()  # releases the capture device (mic indicator off)
        producer.join(timeout=1.0)
