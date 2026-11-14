"""Speak-mode live loop pieces (split from proto.py, #663): confirm / automatic sending, segmenter, shutdown.
proto._live_main wires them together (tests patch them on proto, so it looks them up there)."""
from __future__ import annotations

import sys
import threading
import time
import traceback

from .. import fmt
from .synth import VOICE_ENGINES, _make_synth, _synth_sentences, _target, describe_error, no_audio_reason


def _to_bar(a):
    """Called once per line of my side as it is shown: (zh, ru, lang).
    #322: -> the --overlay bar as one '▶ ' line. #373: meeting mode with --save (after --listen): also appended to
    the listen side's transcript file with side="me", so the file carries both speakers."""
    q = getattr(a, "bar_q", None)
    save = getattr(a, "me_save", None)

    def record(zh, ru, lang=None) -> None:
        if save is not None:
            from ..sink import Result

            save(Result(getattr(a, "src", "zh"), zh, None, ru, tgt=lang or _target(a), side="me"))

    def bar(zh, ru, lang=None, record_row=True) -> None:
        """record_row=False (confirm mode, #494): overlay only; the row is written by bar.record when shown."""
        if q is not None:
            q.put(("final", zh, "▶ " + ru))
        if record_row:
            record(zh, ru, lang)

    bar.record = record
    return bar


def _run_confirm(a, items, mt, tts, player, previewer, res, sess_box=None) -> None:
    """ASR+MT+back-translation in a producer thread; this thread asks for confirmation; TTS runs in the background.
    `items` yields (n, label, zh)."""
    from .confirm import ConfirmSession, Utterance, back_translate
    from .logic import Lat, lat_line, timing_line, translate_ru

    def set_voice(n: int) -> None:
        tts.speaker = n

    sess = ConfirmSession(_make_synth(tts), player.push, previewer.push if previewer.enabled else None,
                          out=lambda m: print(m, flush=True),
                          set_voice=set_voice if getattr(tts, "engine", "") in VOICE_ENGINES else None)
    if sess_box is not None:
        sess_box.append(sess)

    lat = getattr(a, "lat", None) or Lat()
    bar = _to_bar(a)
    show_lat = getattr(a, "debug_latency", False)
    no_back = getattr(a, "no_back", False)
    src = getattr(a, "src", "zh")  # language the user speaks (#134: en -> the other side hears zh)
    gate, pend = threading.Lock(), {}  # per-utterance: the lat dict (produce thread) and tts seconds (synth thread)
    # #207: tts = MT done -> first synthesized part ready to send (was: whole utterance synthesized)
    sess.on_first_part = lambda u: lat_done(u, "tts", lat.clock() - u.extra["_t0"])
    sess.on_show = lambda u: bar.record(u.zh, u.ru, u.lang)  # #494: a dropped/expired line never reaches --save

    def lat_done(u, key, val) -> None:
        """#142: tts finishes after produce() has already taken the lat dict; whichever side arrives last merges
        the two and prints/stores the complete line."""
        with gate:
            st = pend.setdefault(id(u), {})
            st[key] = val
            if "lat" not in st or "tts" not in st:
                return
            del pend[id(u)]
        if st["tts"] is not None:
            st["lat"]["tts"] = round(st["tts"], 3)
        u.extra["lat"] = st["lat"]
        print(timing_line(st["lat"]), file=sys.stderr, flush=True)  # #186 (stderr: the confirm prompt owns stdout)
        if show_lat:
            print(lat_line(st["lat"]), file=sys.stderr, flush=True)

    def produce():
        try:
            for n, label, zh in items:
                lang = _target(a)  # re-read per sentence (--to follow)
                ru, retries = lat.timed("mt", translate_ru, lambda x: mt.translate(x, src, lang).text, zh, lang)
                u = Utterance(n, zh, ru, "", extra={"file": label, "cjk_retries": retries}, lang=lang)
                res["items"].append({"file": label, src: zh, lang: ru, "_u": u})
                bar(zh, ru, lang, record_row=False)  # #494: the save row waits until the line is shown
                u.extra["_t0"] = lat.clock()
                sess.submit(u)  # synthesis starts NOW (#132): the back-translation below no longer delays it
                u.future.add_done_callback(  # no audio at all: report the line without tts_first
                    lambda f, u=u: None if f.cancelled() or u.extra.get("_first_seen") else lat_done(u, "tts", None))
                if not no_back:
                    sess.set_back(u, lat.timed("back", back_translate, mt, ru, lang, src))
                u.extra["lat"] = taken = lat.take()  # partial until tts lands (lat_done merges it)
                lat_done(u, "lat", taken)
        except Exception as e:  # noqa: BLE001
            from ..capture._soundcard import AudioDeviceLost
            if isinstance(e, AudioDeviceLost):  # #317: the retries already explained it; one line, no traceback
                print(f"tlwu: {e}", file=sys.stderr, flush=True)
                return
            # #130: never an empty message (AssertionError has none): name the exception and keep the traceback
            print(f"input stopped: {describe_error(e)}", flush=True)
            traceback.print_exc(file=sys.stderr)
        finally:
            sess.finish()

    th = threading.Thread(target=produce, daemon=True)
    th.start()
    sess.run()
    th.join(5)
    for it in res["items"]:
        u = it.pop("_u", None)
        it["decision"] = u.decision if u else ""
        if u:
            it["back"], it["lat"] = u.back, u.extra.get("lat", {})


def _record_line(lat, on_record, mt_start, shown, first_audio, lang, text) -> None:
    """#434: hand one speak_record to on_record (--latency-jsonl) when the utterance came with capture marks."""
    from .logic import speak_record

    if on_record is not None and {"eos", "cut", "asr_done"} <= set(lat.marks):
        on_record(speak_record(lat.marks, mt_start, shown, first_audio, lang=lang, text=text))


def _run_auto_live(items, mt, tts, player, res, lang="ru", lat=None, show_lat=False, src="zh", bar=None,
                   on_record=None) -> None:
    """--no-confirm with live input: translate + speak every utterance as it arrives. Each sentence goes to the
    player as soon as it is synthesized (#132: the first sentence plays while the rest is still in TTS).
    `lang`: a code, or a callable returning the code for the next sentence (--to follow).
    on_record(dict) (#434, --latency-jsonl): one logic.speak_record per utterance."""
    from .logic import GapMeter, Lat, lat_line, latency_fields, latency_line, stream_line, timing_line, translate_ru

    lat = lat or Lat()
    stamp = (lambda: lat.clock()) if on_record is not None else (lambda: None)  # no extra clock reads otherwise
    for n, label, zh in items:
        lg = lang() if callable(lang) else lang
        mt_start = stamp()
        ru, _ = lat.timed("mt", translate_ru, lambda x: mt.translate(x, src, lg).text, zh, lg)
        print("\n" + fmt.block("me", zh, [(None, ru)], color=fmt.color_enabled(sys.stdout)).rstrip("\n"), flush=True)
        if bar is not None:
            bar(zh, ru, lg)
        shown, first_audio = stamp(), None
        t0, sent = lat.clock(), 0
        gaps, stats, info = GapMeter(lat.clock), [], {}
        for audio, sr in _synth_sentences(tts, ru, stats, info=info):
            if not sent:
                lat.set("tts", lat.clock() - t0)
                print(timing_line(lat.t), flush=True)  # #186: first audio is out; one line under the translation
            gaps.push(len(audio) / sr)
            if not sent:
                first_audio = stamp()  # #434: the first audio chunk is handed to the output now
            player.push(audio, sr)
            sent += 1
        tts_done = lat.clock() - t0
        if sent:  # #207: how the streamed pieces kept up with playback
            print(stream_line(sent, gaps.max, stats), file=sys.stderr, flush=True)
        else:  # #316: never a silent "first_audio=-s"
            print(f"[tts] nothing to speak: {no_audio_reason(info)}", flush=True)
        t = lat.take()
        # #180: one batch-style line per utterance (same fields as the --report items), always on
        f = latency_fields(t.get("asr", 0.0), t.get("mt", 0.0), t.get("tts") if sent else None, tts_done)
        print(latency_line(f), file=sys.stderr, flush=True)
        if show_lat:
            print(lat_line(t), file=sys.stderr, flush=True)
        res["items"].append({"file": label, src: zh, lg: ru, "decision": "sent" if sent else "no_audio", "lat": t, **f,
                             "pieces": sent, "gap_max": gaps.max})
        _record_line(lat, on_record, mt_start, shown, first_audio, lg, ru)


def make_segmenter(max_seg: float, dev: str = "cpu", pause=None, vad_fn=None, env=None):
    """Live speak-mode VAD segmenter: an utterance ends after `pause` s of silence (--pause/--end-silence, #139;
    None = live_endpoint's per-device default). Returns (segmenter, configured silence)."""
    from ..segment import Segmenter
    from .logic import live_endpoint, live_vad_pad

    sil, step = live_endpoint(dev, pause, env)
    extra = {"vad_fn": vad_fn} if vad_fn is not None else {}
    return Segmenter(max_seg, True, min_silence=sil, step=step, vad_pad=live_vad_pad(dev, env), **extra), sil


def paced_file_source(path):
    """#434 --input X --paced: the file released in real time, in the mic's 100 ms blocks (capture/_soundcard).
    Decoded here, before PacedSource starts its clock, so decoding time does not eat into the real-time spacing."""
    from ..capture.base import AudioSource
    from ..capture.file import FileSource
    from ..stream import PacedSource

    src = FileSource(str(path), block_s=0.1)
    audio = src._load()

    class Decoded(AudioSource):
        frames = lambda self: (audio[i:i + src.block] for i in range(0, len(audio), src.block))  # noqa: E731
        close = lambda self: None  # noqa: E731

    return PacedSource(Decoded())


def _drain(*players) -> None:
    """#577: let each enabled player finish its queue. Without this, an input that simply ends (--paced file, the
    speak_selftest clip) lost the last sentence's queued TTS to cleanup()'s abort(): hw-tests recorded 85-89 %."""
    for pl in players:
        if getattr(pl, "enabled", False) and getattr(pl, "close", None) is not None:
            pl.close()


def _close_items(gen, timeout: float = 5.0) -> None:
    """Close the utterance generator. Confirm mode runs it in its producer thread, which Ctrl+C does not wait for:
    close() then raises 'generator already executing' until that thread leaves it (stop is set and the source is
    closed, so within one poll unless MT is mid-sentence). Retry up to `timeout` (the same 5 s _run_confirm gives
    the producer); never raise on shutdown."""
    end = time.monotonic() + timeout
    while True:
        try:
            gen.close()
            return
        except ValueError:
            if time.monotonic() >= end:
                print("tlwu: speech input did not stop in time", file=sys.stderr, flush=True)
                return
            time.sleep(0.05)


def _stop_live(stop, box, tts, player, previewer, source=None):
    """Stop capture, abort confirm sessions, kill the TTS subprocess, mute output. Safe to call twice: once from
    the --overlay watcher (#322: the worker may sit in a confirm prompt) and once when _live_main returns.
    #427: the audio source is closed and the players are aborted, so no thread is left inside PulseAudio when the
    interpreter exits (Linux: hang after 'peak VRAM' / SIGABRT)."""
    def cleanup() -> None:
        stop.set()
        if source is not None:
            try:
                source.close()
            except Exception:  # noqa: BLE001
                pass
        for sess in box:
            sess.abort()
        if tts is not None:
            try:
                tts.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        for pl in (player, previewer):
            abort = getattr(pl, "abort", None)
            if abort is not None:
                abort()
            else:
                pl.enabled = False

    return cleanup
