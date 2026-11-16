from __future__ import annotations

import contextlib
import queue
import re
import sys
import threading
import time
from typing import NamedTuple

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
from .sink import Result, Sink, pass_source
from .speakers import UNKNOWN
from .translate import Translation, Translator, translate_checked

SILENCE_RMS = 1e-3
_DONE = object()


class _Quiet(NamedTuple):
    """#716 queue item: `seconds` of silence after the newest segment (no new speech), at audio position `stamp`."""

    seconds: float
    stamp: float
# Ctrl+C: on Windows a blocking Queue.get() with no timeout is not interruptible, so the main thread
# always waits in short slices and KeyboardInterrupt is delivered within ~POLL seconds.
POLL = 0.25
BACKLOG_POLL = 0.5  # #1259: how often the ASR backlog is re-measured
# #1253: while ASR is at least MT_YIELD_S behind the audio (the #1259 "ASR n s behind" line is showing), MT starts
# no new line, so ASR gets the CPU back (owner field test, 12-CPU laptop: with MT on, ASR dropped ~2x as many
# sentences as ASR-only). The source line is shown at once; its translation waits until ASR has caught up (nothing
# queued, not transcribing), or at most MT_HOLD_MAX per line, so translations still trickle out on a CPU that never
# catches up. A line already being translated is not interrupted.
MT_YIELD_S = 1.0
MT_HOLD_MAX = 30.0
MT_HOLD_POLL = 0.05
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
        on_work=None, on_record=None, recorder=None,  # recorder: #239 --record (record.Recorder)
        seg_gate=None, route=None, on_backlog=None, on_mt_wait=None, mt_yield: bool | None = None) -> None:
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
    seg_gate(seg, transcript) -> bool (#968, speak mode): False drops the segment before it reaches the merger, so
    keyboard noise or a low-confidence Whisper guess never becomes a line that is sent in the user's voice.
    route(lang) -> target (#1164 face-to-face): the target language of one utterance, chosen from its detected language.
    on_backlog(seconds) (#1259): whole seconds ASR is behind the audio (queued segments plus the overrun of the one
    being transcribed); called when the value changes within ~0.5 s, 0 once it has caught up. Never called when off.
    mt_yield (#1253, default: live clock with a translator): while ASR is >= MT_YIELD_S behind, MT starts no new line
    (source lines still show at once; translations follow when ASR catches up, at most MT_HOLD_MAX s late per line).
    on_mt_wait(lines) (#1253): number of lines whose translation is held for ASR; called when it changes, 0 when MT
    resumes. on_record gets the held seconds as "mt_hold".
    """
    tgt0 = tgt
    # #1263: live capture is never held back by ASR. A bounded queue blocked the capture reader while ASR lagged and
    # the device then dropped audio with no trace; segments wait here instead (raw 16 kHz float32: 64 KB per second).
    # Offline files (audio_clock) keep the bound: they are read faster than real time and lose nothing by waiting.
    q: queue.Queue = queue.Queue(maxsize=8 if audio_clock else 0)
    consumed = [0]
    frame_t = [0.0]  # monotonic arrival time of the newest frame (#323)
    stop = threading.Event()
    queued_s = [0.0]  # #1259: seconds of audio in segments waiting for ASR
    asr_start = [None, 0.0]  # #1259: (monotonic start, audio seconds) of the segment in ASR
    bl_lock = threading.Lock()

    def asr_lag() -> float:
        with bl_lock:
            lag = queued_s[0]
            if asr_start[0] is not None:
                lag += max(0.0, time.monotonic() - asr_start[0] - asr_start[1])
        return lag

    def backlog_seconds() -> int:
        return int(asr_lag())

    def asr_idle() -> bool:
        """#1253: nothing waits for ASR and no segment is being transcribed."""
        with bl_lock:
            return queued_s[0] < 0.05 and asr_start[0] is None

    def watch_backlog():
        last = 0
        while not stop.wait(BACKLOG_POLL):
            n = backlog_seconds()
            if n != last:
                last = n
                on_backlog(n)

    def counted(frames):
        for f in frames:
            if stop.is_set():  # Stop pressed: end the capture generator even if no segment is ever emitted
                return
            consumed[0] += len(f)
            frame_t[0] = time.monotonic()
            yield f

    def produce():
        try:
            # #716: silence after the newest segment goes through the queue, so it reaches the merger after that text
            if merger is not None and hasattr(merger, "quiet") and hasattr(segmenter, "on_quiet"):
                segmenter.on_quiet = lambda sec: put(_Quiet(sec, consumed[0] / SAMPLE_RATE))
                send = getattr(merger, "pause_send", None)
                if send is not None and hasattr(segmenter, "quiet_max"):  # an explicit --pause-send must be reachable
                    segmenter.quiet_max = max(segmenter.quiet_max, float(send))
            frames = source.frames()
            try:
                for seg in segmenter.segments(counted(frames)):
                    if stop.is_set():
                        return
                    pause = float(getattr(segmenter, "last_pause", 0.0))
                    with bl_lock:
                        queued_s[0] += len(seg) / SAMPLE_RATE
                    # #1252: the segment's own end on the audio clock (a pause cut ends before the input read so far)
                    end_s = (consumed[0] - int(getattr(segmenter, "last_trail", 0) or 0)) / SAMPLE_RATE
                    if not put((seg, end_s, getattr(segmenter, "epoch", 1) - 1,
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
    clip = [None]  # #239: audio of the line being written (MT thread)
    seg_n = [0]  # #239: number of the newest transcribed segment (matches a final line to its audio)
    last_who = [None]  # #384 --speakers: speaker label of the newest transcribed segment (None: off / unknown)
    # #323: a sink that does not override write_source shows no source line, so it gets no t_source_shown
    shows_source = sink_shows_source(sink)
    say = log or (lambda m: None)
    if log is not None and draft is not None and getattr(draft, "log", None):
        say = lambda m: draft._log(m.removeprefix("latency: "))  # noqa: E731 - clears the draft block first

    def write(r: Result, ep) -> None:
        if draft is not None:
            draft.final_written(ep)  # the final line replaces the dim draft
        sink.write(r)
        if recorder is not None:
            recorder.line(clip[0], r)

    def emit(unit_text: str, lang: str, ep, queued: float, who: str | None = None) -> None:
        tgt = route(lang) if route is not None else tgt0
        if translator is None:  # transcript-only
            write(Result(lang, unit_text, None, "", tgt, speaker=who), ep)
            return
        if lang == tgt:  # e.g. Chinese heard while translating to zh: show as is (Traditional via OpenCC)
            if tgt == "zh":
                from .translate import zh_convert

                unit_text = zh_convert(unit_text, getattr(translator, "zh_variant", "tw"))
            write(Result(lang, unit_text, None, unit_text, tgt, speaker=who), ep)
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
        write(Result(lang, unit_text, tr.pivot, tr.text, tgt, speaker=who), ep)

    missing: set = set()  # (src, tgt) pairs already reported as not installed
    mtq: queue.Queue = queue.Queue()  # units waiting for MT + output; unbounded (short text)
    failed: list[BaseException] = []
    yield_on = (not audio_clock and translator is not None) if mt_yield is None else bool(mt_yield and translator)
    asr_over = threading.Event()  # #1253: the ASR loop ended (end of input / Stop): no reason to hold MT any more
    waiting = [0]  # #1253: lines held for ASR (on_mt_wait)

    def stopping() -> bool:
        """#1253: the internal stop or the caller's Stop (GUI) - set while ASR may still be busy in a long call."""
        return stop.is_set() or (stop_event is not None and stop_event.is_set())

    def tell_wait(n: int) -> None:
        if n != waiting[0]:
            waiting[0] = n
            if on_mt_wait is not None:
                on_mt_wait(n)

    def yield_to_asr(item) -> float:
        """#1253: hold this line's MT while ASR is behind; returns the seconds held (0 when MT ran at once)."""
        if not yield_on or asr_over.is_set() or asr_lag() < MT_YIELD_S:
            return 0.0
        lang = item[1]
        if lang == (route(lang) if route is not None else tgt0):
            return 0.0  # shown as is, no MT
        t0 = time.monotonic()
        until = item[3] + MT_HOLD_MAX  # from when the line was queued: time behind an earlier held line counts too
        while (not stopping() and not asr_over.is_set() and not asr_idle()
               and time.monotonic() < until):
            tell_wait(mtq.qsize() + 1)
            time.sleep(MT_HOLD_POLL)
        held = time.monotonic() - t0
        say(f"latency: hold mt {held * 1000:.0f} ms for ASR ({mtq.qsize()} more line(s) waiting)")
        return held

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
                held = yield_to_asr(item)
                tell_wait(0)
                if stopping():
                    return  # Stop during the hold: start no translation or output after run() returned
                t_emit = time.monotonic()
                clip[0] = item[9]  # #239: audio of this line (only the MT thread reads it)
                if draft is not None:
                    draft.last_first_draft = None  # #511: final_written() fills it while emit() writes the line
                emit(*item[:4], who=item[8])
                if on_record is not None and item[6] is not None:
                    rec = _record(item, time.monotonic())
                    d = getattr(draft, "last_first_draft", None) if draft is not None else None
                    rec["draft"] = None if d is None else round(d, 3)  # #511: seconds to the first draft, null = none
                    rec["mt_hold"] = round(held, 3)  # #1253: part of "mt" spent waiting for ASR to catch up
                    on_record(rec)
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
            audio = None
            if recorder is not None and u.tag is not None:  # #239: a dropped line drops its audio too
                audio = recorder.take((u.first or u.tag)[2], u.tag[2])
            if not text:
                continue
            key, now = _dup_key(text), consumed[0] / SAMPLE_RATE if audio_clock else time.monotonic()
            if key and key == last_final[0] and now - last_final[1] <= DUP_WINDOW:
                continue  # identical final line again within the window: show it once
            last_final[0], last_final[1] = key, now
            lang = script_lang(text, u.lang)  # kana labelled zh is Japanese: translate it, never pass it through (#86)
            t_src = None
            # the unit's own segment (marks, speaker), not the newest one
            marks, who = u.tag[:2] if u.tag is not None else (last_marks[0], last_who[0])
            if translator is not None and draft is None and lang != (route(lang) if route is not None else tgt) and hasattr(sink, "write_source"):
                pass_source(sink, lang, text, who)  # #53: the source line now, its translation when MT is done
                t_src = time.monotonic() if shows_source else None
            mtq.put((text, lang, last_ep[0], time.monotonic(), last_asr[0], last_len[0], marks, t_src, who, audio))

    def check_worker() -> None:
        if failed:
            raise failed[0]

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    if on_backlog is not None:
        threading.Thread(target=watch_backlog, daemon=True).start()
    worker = threading.Thread(target=mt_worker, daemon=True)
    worker.start()
    if draft is not None:
        if merger is not None and hasattr(draft, "held"):  # #273 re-translation: drafts show, finals wait for a
            merger.whole = True  # whole sentence (a timeout cuts at a clause boundary, never mid-clause)
            draft.held = lambda: (merger.buf, merger.lang)
        if yield_on:  # #1253: drafts do not compete with a lagging ASR either (a running draft is not interrupted)
            draft.asr_behind = lambda: asr_lag() >= MT_YIELD_S
        draft.start(stop)
    carry = []  # #1252: the undecoded tail of a budget-cut segment, transcribed before the next queued segment
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
                item = carry.pop() if carry else q.get(timeout=timeout)
            except queue.Empty:
                if has_deadline:
                    emit_units(merger.due(time.monotonic()))
                continue
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, _Quiet):
                if translator is not None:
                    emit_units(merger.quiet(item.seconds, item.stamp if audio_clock else time.monotonic()))
                continue
            seg, stamp, last_ep[0], forced_cut, pause, (t_eos, t_cut) = item
            with bl_lock:
                queued_s[0] = max(0.0, queued_s[0] - len(seg) / SAMPLE_RATE)
            if float(np.sqrt(np.mean(seg ** 2))) < SILENCE_RMS:
                continue  # silence: skip (avoids Whisper hallucinations)
            with model_lock():
                t0 = time.monotonic()
                with bl_lock:
                    asr_start[0], asr_start[1] = t0, len(seg) / SAMPLE_RATE
                pin = getattr(asr, "pinned", None)
                cur = pin() if callable(pin) else asr  # #1295: one model for transcribe and its pop_tail
                try:
                    t = cur.transcribe(seg)
                finally:
                    with bl_lock:
                        asr_start[0] = None
                pop_tail = getattr(cur, "pop_tail", None)
                tail = pop_tail() if callable(pop_tail) else None
            if tail is not None and len(tail):  # #1252: a budget cut left the end of this segment undecoded:
                # transcribe it next, before any newer segment (lost source text cannot be recovered; late is fine)
                carry.append((tail, stamp, last_ep[0], forced_cut, pause, (t_eos, t_cut)))
                with bl_lock:
                    queued_s[0] += len(tail) / SAMPLE_RATE
            last_asr[0] = time.monotonic() - t0
            marks = (t_eos, t_cut, t0 + last_asr[0])
            last_len[0] = len(seg) / SAMPLE_RATE
            say(f"latency: asr {last_asr[0] * 1000:.0f} ms for {len(seg) / SAMPLE_RATE:.1f} s of audio"
                f" ({mtq.qsize()} line(s) waiting for MT)")
            if t is None or is_hallucination(t.text, float(np.sqrt(np.mean(seg ** 2))), t.no_speech):
                continue  # nothing heard, or a stock Whisper subtitle-credit line on music/silence
            if seg_gate is not None and not seg_gate(seg, t):
                continue  # #968: speak input gate (noise, no_speech, low logprob, filler, loop)
            last_marks[0] = marks
            if speakers is not None:  # --speakers (#222): label this sentence; #384: shown on the line itself,
                who, spk = speakers.label(seg)  # the label goes to the debug log only (no extra console line)
                if who != UNKNOWN or spk:
                    say(f"latency: spk {spk:.3f} s ({who})")
                last_who[0] = None if who == UNKNOWN else who
            seg_n[0] += 1
            if recorder is not None:
                recorder.add(seg_n[0], seg, stamp - len(seg) / SAMPLE_RATE, stamp)
            tag = (marks, last_who[0], seg_n[0])
            if merger is None or translator is None:
                emit_units([Unit(t.text, t.lang, False, tag)])
                continue
            emit_units(merger.add(t.text, script_lang(t.text, t.lang), stamp if audio_clock else time.monotonic(),
                                   forced_cut=forced_cut, pause=pause, tag=tag))
        if merger is not None and translator is not None:
            emit_units(merger.flush())
        asr_over.set()
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
        asr_over.set()
        mtq.put(_DONE)
        if draft is not None:
            draft.renderer.clear()
        source.close()  # releases the capture device (mic indicator off)
        producer.join(timeout=1.0)
