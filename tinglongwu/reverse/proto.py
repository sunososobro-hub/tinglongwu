"""Reverse-mode prototype: Chinese wav(s) -> Whisper (zh) -> Hy-MT2 zh->ru -> built-in-voice TTS (Vosk-TTS / Kokoro, subprocess) -> speakers + wav.

Run (main .venv):
  python -m tinglongwu.reverse.proto --input C:\\...\\reverse\\input --out C:\\...\\reverse\\out \\
      --tts-python C:\\...\\tts-venv\\Scripts\\python.exe --tts-model C:\\...\\models\\tts-model \\
      --ref ref_ru.wav --ref-text "..." [--device "CABLE Input"] [--no-play]
Results are written (UTF-8 JSON) only when --save-result PATH is given; nothing is saved by default.
Default = confirm-before-send: each utterance shows zh / ru / back-translation; "y"+Enter sends the Russian audio to
--device, a bare Enter (or "r"/"x") discards it (--no-confirm = old automatic behaviour; --preview-device = also hear it).
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from .. import fmt


def vram_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


class VramSampler:
    def __init__(self, period: float = 0.25):
        self.period, self.peak, self._stop = period, 0, threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            v = vram_used_mib()
            if v:
                self.peak = max(self.peak, v)
            self._stop.wait(self.period)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self.t.join(2)


PLAY_BLOCK_S = 0.1  # #427: a piece is written in blocks this long, so abort() is noticed within one block
ABORT_JOIN_S = 2.0  # after abort(): wait for the playback thread (one PLAY_BLOCK_S block + closing the stream)


class Player:
    """Plays 24 kHz mono float chunks on the chosen output device (resampled to `out_sr`) from a worker thread."""

    def __init__(self, device: str | None, out_sr: int = 48000, enabled: bool = True):
        self.out_sr, self.enabled, self.q = out_sr, enabled, queue.Queue()
        # macOS: BlackHole 16ch has 16 channels; a mono stream may reach only channel 1, so send identical L/R
        self.ch = 2 if sys.platform == "darwin" else 1
        self.speaker_name = None
        from .live import PlaybackGate

        self.gate = PlaybackGate()
        self._abort = threading.Event()
        if enabled:
            import soundcard as sc

            from ..capture._soundcard import patch_mixformat
            from .logic import match_device
            from .vmic import loop_prone_output

            patch_mixformat()  # #130: players hit the same soundcard mix-format assert as recorders
            speakers = sc.all_speakers()
            idx = match_device([s.name for s in speakers], device)
            spk = speakers[idx] if idx is not None else sc.default_speaker()
            self.speaker_name = spk.name
            if sys.platform == "darwin" and loop_prone_output(spk.name):
                raise RuntimeError(
                    f"output device {spk.name!r} is the system-audio capture path (it would be heard by the "
                    f"listen side and loop back). Pass --device with your headphones / 'BlackHole 16ch' "
                    f"(see --list-speakers).")
            self.t = threading.Thread(target=self._run, args=(spk,), daemon=True)
            self.t.start()

    def _run(self, spk):
        with spk.player(samplerate=self.out_sr, channels=self.ch, blocksize=1024) as p:
            while True:
                item = self.q.get()
                if item is None or self._abort.is_set():
                    if item is not None:
                        self.gate.end()
                        continue  # drop what was queued before / during abort(); the None sentinel follows
                    return
                try:
                    data = np.repeat(item.reshape(-1, 1), self.ch, axis=1)
                    step = max(1, int(self.out_sr * PLAY_BLOCK_S))
                    for i in range(0, len(data), step):
                        if self._abort.is_set():  # #427: leave a long piece within one block on Ctrl+C
                            break
                        p.play(data[i:i + step])
                finally:
                    self.gate.end()

    def push(self, audio: np.ndarray, sr: int) -> None:
        if self.enabled:
            from .logic import resample

            self.gate.start()
            self.q.put(resample(audio, sr, self.out_sr))

    def abort(self) -> None:
        """#427 (Ctrl+C): mute, drop the queued audio and let the playback thread leave the output stream."""
        self.enabled = False
        self._abort.set()
        if getattr(self, "t", None) is not None:
            self.q.put(None)  # the thread drops anything queued before it (gate released per item)

    def close(self, timeout: float = 60) -> None:
        """Play what is queued, then stop the playback thread. After abort() it only waits for that thread (#427:
        a playback thread still inside the PulseAudio stream at interpreter exit could hang the process)."""
        t = getattr(self, "t", None)
        if t is None:
            return
        if self.enabled:
            self.q.put(None)
        t.join(timeout if self.enabled else min(timeout, ABORT_JOIN_S))
        if t.is_alive():
            print("tlwu: audio output did not stop in time", file=sys.stderr, flush=True)


def _file_items(wavs, sf, asr):
    """(n, label, zh) for each wav file with recognised speech."""
    for n, wav in enumerate(wavs, 1):
        audio, sr = sf.read(str(wav), dtype="float32")
        assert sr == 16000, f"{wav}: expected 16 kHz"
        tr = asr.transcribe(audio)
        if tr and tr.text:
            yield n, wav.name, tr.text


VOICE_ENGINES = ("vosk", "kokoro")  # engines with built-in voices (v0-v4 switch)


def _synth_sentences(tts, ru: str, stats: list | None = None, clock=None, on_first=None, info: dict | None = None):
    """Yield (audio, sr) one piece at a time, so the first piece can play while the rest is synthesized. #207: the
    first piece is a short clause (PiecePlanner), so the first audio does not wait for a long first sentence; each
    later piece is sized from this worker's measured speed (chars/s) so it is ready before the audio queued ahead
    of it has played (no gap), and is as long as that allows (fewer cuts). The worker's 'done' events are appended
    to `stats` when given; on_first(seconds) is called once, when the first piece is ready (before it is yielded).
    #316: `info` (a dict, when given) gets "pieces" (TTS requests sent) and "errors" (worker error messages), so a
    caller that got no audio can say why."""
    from .logic import PiecePlanner, next_budget, tts_text

    if info is not None:
        info.setdefault("pieces", 0)
        info.setdefault("errors", [])

    clock = clock or time.perf_counter
    t0 = clock()
    plan, end, cps = PiecePlanner(ru), None, None  # end: when the audio handed out so far finishes playing
    budget = None
    while (sent := plan.next(budget)) is not None:
        t1 = clock()
        pcm, sr_out = [], 24000
        if info is not None:
            info["pieces"] += 1
        for ev, data in tts.speak(tts_text(sent)):
            if ev["t"] == "chunk":
                if data:
                    pcm.append(np.frombuffer(data, dtype=np.float32))
                sr_out = ev["sr"]
            elif ev["t"] == "done" and stats is not None:
                stats.append(ev)
            elif ev["t"] == "error" and info is not None:
                info["errors"].append(str(ev.get("msg") or "unknown error"))
        now = clock()
        if now > t1:  # wall time incl. the pipe: what the next piece will really cost
            c = len(sent) / (now - t1)
            cps = c if cps is None else min(c, 0.5 * (cps + c))  # pessimistic: a slow piece counts at once
        if pcm:
            audio = np.concatenate(pcm)
            if end is None and on_first is not None:
                on_first(now - t0)
            end = max(now, end or now) + len(audio) / sr_out
            yield audio, sr_out
        budget = next_budget((end or clock()) - clock(), cps)


# A full-length sentence, not "Hello.": onnxruntime's first runs at a new input length are slower (allocations),
# measured: the first real utterance after a 1-word warm-up still had a 0.6-0.7 s gap between its pieces
WARM_TEXT = {"ru": "Здравствуйте, давайте начнём сегодняшнюю встречу и обсудим планы на следующую неделю.",
             "ja": "皆さん、こんにちは。今日の会議を始めましょう。",
             "zh": "大家好，我們現在開始今天的會議，先看一下上週的進度。"}
WARM_EN = "Hello everyone, let's begin today's meeting and look at the plans for next week."


def warm_tts(tts, lang: str) -> None:
    """#207: one throw-away request at startup (phonemizer / onnxruntime first-run cost), so the first utterance of
    the session is not slower than the rest. Failures are left to the real requests to report."""
    try:
        for _ in tts.speak(WARM_TEXT.get(lang, WARM_EN)):
            pass
    except Exception:  # noqa: BLE001
        pass


def _make_synth(tts):
    """Confirm-mode synth: a generator of sentence parts, run by the session's TTS worker; after "y" each part is
    sent as soon as it exists, so sentence 1 plays while the rest is still synthesized (#139)."""
    infos: dict = {}  # ru text -> info of its latest synthesis (#368: why no audio)

    def synth(ru: str):
        info = infos[ru] = {}
        return _synth_sentences(tts, ru, info=info)

    synth.no_audio_reason = lambda ru: no_audio_reason(infos.get(ru) or {})  # type: ignore[attr-defined]
    return synth


def _target(a) -> str:
    """Spoken language for the next sentence; with --to follow, whatever the other side spoke last (default en)."""
    to = getattr(a, "to", "ru")
    if to == "follow":
        from ..langfollow import default_follow_path, read_lang

        return read_lang(getattr(a, "follow_file", None) or default_follow_path(), "en")
    return to


_CYR = re.compile("[Ѐ-ӿ]")
_JA = re.compile("[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")  # kana / ideographs (en and ru output never has them)


class FollowTTS:
    """TTS front for --to follow: Russian (Cyrillic) text goes to the Vosk worker, anything else to Kokoro
    (Japanese text is Kokoro too, with lang "ja": same worker process, misaki loads on the first ja sentence).
    Each worker subprocess is started on first use (`factories`: {"en": callable, "ru": callable})."""

    engine = "vosk"  # both built-in-voice engines: v0-v4 switching works
    ready: dict = {}

    def __init__(self, factories: dict):
        self.factories, self.clients, self._speaker = factories, {}, None

    def client(self, lang: str):
        if lang not in self.clients:
            print(f"loading {lang} voice...", flush=True)
            c = self.factories[lang]()
            if self._speaker is not None:
                c.speaker = self._speaker
            self.clients[lang] = c
        return self.clients[lang]

    @property
    def speaker(self):
        return self._speaker

    @speaker.setter
    def speaker(self, n: int) -> None:
        self._speaker = n
        for c in self.clients.values():
            c.speaker = n

    def speak(self, text: str):
        if _CYR.search(text):
            if "ru" not in self.factories:  # Russian voice model not installed (opt-in): subtitles only
                return iter(())
            return self.client("ru").speak(text)
        if _JA.search(text):
            return self.client("en").speak(text, lang="ja")
        return self.client("en").speak(text, lang="en")

    @property
    def proc(self):
        return self

    def kill(self) -> None:
        for c in self.clients.values():
            try:
                c.proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        for c in self.clients.values():
            c.close()


def no_audio_reason(info: dict) -> str:
    """#316: one short reason why a translated utterance produced no audio (info from _synth_sentences)."""
    if info.get("errors"):
        return f"TTS error: {info['errors'][0]}"
    if not info.get("pieces"):
        return "no speakable text in the translation"
    return "the voice returned no audio (voice for this language not installed?)"


def describe_error(e: BaseException) -> str:
    """'Type: message' (just the type when the message is empty, e.g. a bare AssertionError)."""
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


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


PAUSE_MIN, PAUSE_MAX = 0.3, 2.0  # silero_vad merges gaps < 300 ms (segment.py), so a shorter tail cannot be heard


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


def _live_main(a, asr, mt, tts, player, previewer, confirm, res) -> None:
    """Mic (or #434: --input X --paced, a file in real time) -> VAD segments -> Whisper(zh) -> same pipeline.
    Ctrl+C stops capture and kills the TTS subprocess. a.on_record (#434 --latency-jsonl): one record per line."""
    from ..capture.mic import MicSource
    from .live import capture_segments, recognize_utterances

    from .logic import Lat, TimedASR, timing_line

    stop = getattr(a, "ext_stop", None) or threading.Event()  # #322: --overlay's Quit / Ctrl+C sets it
    src = getattr(a, "src", "zh")
    bar = _to_bar(a)
    paced = bool(getattr(a, "paced", False))
    on_record = getattr(a, "on_record", None)
    seg, sil = make_segmenter(a.chunk_s, getattr(a, "dev", "cpu"), getattr(a, "end_silence", None))
    a.lat = lat = Lat(fixed={"vad": sil})  # configured end-of-speech silence, shown as vad= in the lat line (#139)
    asr = TimedASR(asr, lat) if asr is not None else None
    source = paced_file_source(a.input) if paced else MicSource(a.mic_name)
    label = Path(a.input).name if paced else "mic"

    def items():
        for n, zh in enumerate(recognize_utterances(capture_segments(source, seg, stop, lat), asr,
                                                    gate=None if paced else getattr(player, "gate", None), src=src,
                                                    zh_variant=getattr(a, "zh_variant", "tw")), 1):
            lat.marks["asr_done"] = lat.clock()  # #434: text of the newest segment is ready
            yield n, label, zh

    box: list = []
    cleanup = _stop_live(stop, box, tts, player, previewer, source)
    if getattr(a, "ext_stop", None) is not None:  # #322: the bar's Quit must also end a pending confirm prompt
        threading.Thread(target=lambda: (stop.wait(), cleanup()), name="tlwu-speak-stop", daemon=True).start()
    if paced:
        print(f"replaying {a.input} in real time (--paced)...", flush=True)
    else:
        print(f"listening... speak {'English' if src == 'en' else 'Chinese'}; Ctrl+C to quit", flush=True)
    utterances = items()
    try:
        if tts is None:  # text only: show zh -> ru, nothing is spoken
            from .logic import translate_ru

            for _n, _src, zh in utterances:
                lang = _target(a)
                mt_start = lat.clock()
                ru, _r = lat.timed("mt", translate_ru, lambda x: mt.translate(x, src, lang).text, zh, lang)
                print(fmt.block("me", zh, [(None, ru)], color=fmt.color_enabled(sys.stdout)), end="", flush=True)
                bar(zh, ru, lang)
                shown = lat.clock()
                print(timing_line(lat.take(), tts=False), flush=True)  # #186
                _record_line(lat, on_record, mt_start, shown, None, lang, ru)
        elif confirm:
            _run_confirm(a, utterances, mt, tts, player, previewer, res, box)
        else:
            _run_auto_live(utterances, mt, tts, player, res, lambda: _target(a), lat,
                           getattr(a, "debug_latency", False), src, bar, on_record)
        if paced and getattr(player, "close", None) is not None:  # the file ended: let the queued audio finish
            player.close()
    except KeyboardInterrupt:
        print("\nstopping...", flush=True)
    finally:
        try:
            cleanup()
        finally:
            _close_items(utterances)  # #427: ends capture_segments, which joins the capture thread (recorder released)


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


# CPU expectation printed at startup (#331, acceptance 4b): Hy-MT2-1.8B Q4_K_M on a 4-core CPU takes about
# 1.5 s per sentence (#227 menu text, #186 Linux CPU speak latency ~4 s end to end); minimum spec in #280.
HYMT_CPU_NOTE = ("Hy-MT2 runs on the CPU here: expect about 1-1.5 s of translation per sentence "
                 "(~4 s from end of speech to audio, #186); a CUDA GPU with 2 GB free VRAM is much faster.")


def choose_speak_mt(kind: str, device: str, mdir, free_mib=None) -> tuple[str, str, str]:
    """(engine, mt_device, reason) for speak mode. Hy-MT2 is the only engine (#331).

    Hy-MT2 runs on CUDA when the GPU has >= engines.HYMT_MIN_FREE_MIB free (#66), else on the CPU. A missing GGUF is
    a ModelNotFound with the `tlwu-setup --hymt` command (no silent fallback)."""
    from ..engines import HYMT_MIN_FREE_MIB, free_vram_mib
    from ..hymt import hymt_path
    from ..models import require

    require(hymt_path(mdir), "Hy-MT2 translation model (the only speak-mode engine)", "--hymt")
    if device != "cuda":
        return "hymt", "cpu", "CPU mode"
    free = (free_mib or free_vram_mib)()
    if free is None:
        return "hymt", "cpu", "could not read free VRAM: Hy-MT2 on the CPU"
    if free < HYMT_MIN_FREE_MIB:
        return "hymt", "cpu", f"{free} MiB VRAM free < {HYMT_MIN_FREE_MIB} MiB: Hy-MT2 on the CPU"
    return "hymt", "cuda", f"{free} MiB VRAM free >= {HYMT_MIN_FREE_MIB} MiB"


def _mt_kind(v: str) -> str:
    """--mt value: auto | hymt. opus / qwen were removed (#331, #283): argparse exits 2 with how to fix old launchers."""
    if v in ("auto", "hymt"):
        return v
    raise argparse.ArgumentTypeError(f"{v!r} was removed (#331): Hy-MT2 is the only engine; use --mt auto "
                                     "(re-run the installer to regenerate an old tlwu-talk launcher)")


class _UnsupportedPair(RuntimeError):
    """Hy-MT2 loaded, but a language of the pair is not one it translates."""


class _NoFallback:
    """Base translator under JaRouter: Hy-MT2 is the only engine (#331), so a pair it cannot take, or a Hy-MT2 that
    failed mid-session, is an error with the reason instead of a silent engine switch."""

    def __init__(self):
        self.router = None
        self.glossary = None

    def _why(self) -> str:
        err = getattr(self.router, "hy_error", "") or "language not supported by Hy-MT2"
        return f"Hy-MT2 cannot translate this ({err[:160]}); there is no fallback engine (#331)"

    def preload(self, src: str, tgt: str) -> None:
        if getattr(self.router, "hy", None) is not None:
            raise _UnsupportedPair(self._why())
        raise RuntimeError(self._why())

    def translate(self, text: str, src: str, tgt: str):
        raise RuntimeError(self._why())

    draft = translate


def _router_warn(m: str, warn) -> None:
    """JaRouter warnings under _NoFallback: the pivot wording is wrong here, so a mid-session Hy-MT2 failure becomes
    one "Hy-MT2 stopped (reason)" line (#451); other pivot warnings are dropped."""
    if "Hy-MT2 failed (" in m:
        warn(f"tlwu: Hy-MT2 stopped ({m.split('Hy-MT2 failed (', 1)[1].split('); ', 1)[0]}); no fallback engine (#331)")
    elif "pivot" not in m:
        warn(m)


def build_translator(kind: str, device: str, mdir, to: str = "ru", follow_langs=("en", "ru"),
                     src: str = "zh", free_mib=None, hy_factory=None, warn=None):
    """zh->ru|en|ja (or en->zh with src en, #134) translator plus the reverse pair for the back-translation shown
    while confirming. Hy-MT2 direct for every pair (#331: no pivot, no other engine). Not installed -> ModelNotFound
    with `tlwu-setup --hymt`; installed but not loadable (llama-cpp-python missing/broken) -> SystemExit, one line."""
    from ..hymt import JaRouter

    kind, mt_dev, why = choose_speak_mt(kind, device, mdir, free_mib)
    print(f"translation engine: {kind} on {mt_dev} ({why})", flush=True)
    warn = warn or (lambda m: print(m, file=sys.stderr, flush=True))
    if mt_dev == "cpu":
        warn(HYMT_CPU_NOTE)
    base = _NoFallback()
    # JaRouter's own "falls back to the ... pivot" warnings are wrong here: _NoFallback raises with the reason instead
    mt = JaRouter(base, mdir, "tw", mt_dev, all_pairs=True, warn=lambda m: _router_warn(m, warn),
                  **({"factory": hy_factory} if hy_factory else {}))
    base.router = mt
    targets = tuple(follow_langs) if to == "follow" else (to,)
    try:
        for t in targets:  # follow: every spoken language
            mt.preload(src, t)  # fail early if the model cannot load
            mt.preload(t, src)  # back-translation for the confirm step
    except Exception as e:  # noqa: BLE001 - incl. OSError from a --to ja preload; KeyboardInterrupt passes
        if isinstance(e, _UnsupportedPair):  # Hy-MT2 is fine, the language is not (e.g. --follow-langs): no install hint
            raise SystemExit(f"tlwu: {e}") from e
        from ..llamacheck import FIX_HINT, INDEX, LLAMA_VERSION

        wheel = f"{INDEX}/cu124" if mt_dev == "cuda" else f"{INDEX}/cpu"   # keep GPU support on a CUDA machine
        raise SystemExit(f"tlwu: {e}. Not installed: pip install llama-cpp-python=={LLAMA_VERSION} --only-binary=:all: "
                         f"--extra-index-url {wheel} && tlwu-setup --hymt. Installed but broken: {FIX_HINT}") from e
    mt.history, mt._pair = [], None  # same surface as the other translators for confirm.back_translate
    return mt


def _ru_model_ok(path) -> bool:
    return bool(path) and Path(path).is_dir()


def _warn_no_ru_model() -> None:
    from ..i18n import t

    print(t("vosk_ru_license"), file=sys.stderr)
    print(t("vosk_ru_missing"), file=sys.stderr, flush=True)


def resolve_tts_engine(a) -> None:
    """--tts-engine auto -> a concrete engine, in place (#128). Russian prefers Piper (CC0 voices) over Vosk (no
    stated model license): an explicit --tts-model that holds a Piper voice, or with no --tts-model the installed
    `tlwu-setup --piper-ru` voice; otherwise Vosk, as before (other targets still need --tts-engine kokoro).
    --to follow: the Russian worker is Piper when --vosk-model is a Piper voice (or is empty and one is installed)."""
    from ..models import default_piper_ru, default_vosk_ru, piper_onnx

    mdir = getattr(a, "models_dir", None)
    if a.to == "follow":
        if not a.vosk_model and (d := default_piper_ru(mdir) or default_vosk_ru(mdir)):
            a.vosk_model = str(d)
        a.ru_engine = "piper" if piper_onnx(a.vosk_model) else "vosk"
        return
    if a.tts_engine == "auto":
        if a.to == "ru" and getattr(a, "voice", None) and a.speaker is None and not a.tts_model and default_vosk_ru(mdir):
            a.tts_engine = "vosk"  # #319: --voice picks among the Vosk voices (the Piper ones are male only)
        elif a.to != "ru":
            a.tts_engine = "vosk"  # unchanged default: rejected below unless --no-tts
        elif piper_onnx(a.tts_model):
            a.tts_engine = "piper"
        elif not a.tts_model and (d := default_piper_ru(mdir)):
            a.tts_engine, a.tts_model = "piper", str(d)
        else:
            a.tts_engine = "vosk"
    if a.tts_engine == "vosk" and a.to == "ru" and not a.tts_model and (d := default_vosk_ru(mdir)):
        a.tts_model = str(d)  # #311: the model `tlwu-setup --vosk-tts-ru` installed (or VOSK_MODEL), no flag needed


def check_args(a) -> str | None:
    """Validate/normalise cross-option rules in place; returns an error message or None."""
    if a.to is None:  # #134: --to follows --from (en -> zh); must be set before the TTS engine is resolved
        a.to = "zh" if getattr(a, "src", "zh") == "en" else "ru"
    pause = getattr(a, "end_silence", None)
    if pause is not None and not PAUSE_MIN <= pause <= PAUSE_MAX:
        return f"--pause/--end-silence takes {PAUSE_MIN}-{PAUSE_MAX} seconds, got {pause}"
    resolve_tts_engine(a)
    langs = tuple(x.strip() for x in getattr(a, "follow_langs", "en,ru").split(",") if x.strip())
    bad = [x for x in langs if x not in ("en", "ru", "ja")]
    if bad or not langs:
        return f"--follow-langs takes en, ru, ja (comma separated), got {a.follow_langs!r}"
    a.follow_set = langs
    a.ru_voice = getattr(a, "ru_voice", True)  # check_args may run twice; never re-enable
    src = getattr(a, "src", "zh")
    if src == "en" and a.to != "zh":
        return "--from en speaks Chinese to the other side: use --to zh (or leave --to out)"
    if src == "zh" and a.to == "zh":
        return "--to zh needs --from en (you speak English, the other side hears Chinese)"
    if not hasattr(a, "speaker_given"):  # check_args may run twice: the second pass sees the default filled in
        a.speaker_given = a.speaker is not None
    if a.to == "follow" and getattr(a, "voice", None):  # #319: the probe would also switch the English voice
        if not a.speaker_given:
            return "--voice picks a Vosk Russian voice: use --to ru (with --to follow use --speaker N)"
        print(f"tlwu: --speaker {a.speaker} given, --voice {a.voice} ignored", file=sys.stderr, flush=True)
        a.voice = None
    if a.to == "follow":
        if not a.no_tts:
            if a.tts_engine != "kokoro" or not a.tts_model:
                return ("--to follow needs --tts-engine kokoro (+ --tts-python/--tts-model for English and "
                        "Japanese), or --no-tts")
            if "ru" in langs and not _ru_model_ok(a.vosk_model):  # opt-in model absent: no Russian voice
                _warn_no_ru_model()
                a.ru_voice = False
        return None
    from ..models import piper_onnx

    if getattr(a, "voice", None) and not a.no_tts:
        if a.speaker_given:  # #319: an explicit id wins
            print(f"tlwu: --speaker {a.speaker} given, --voice {a.voice} ignored", file=sys.stderr, flush=True)
            a.voice = None
        elif a.tts_engine != "vosk" or a.to != "ru":
            return "--voice picks a Vosk Russian voice: use --to ru with --tts-engine vosk (or --speaker N)"
    if a.speaker is None:
        a.speaker = 0 if a.tts_engine == "kokoro" else 4
    if a.tts_engine == "piper" and a.to != "ru" and not a.no_tts:
        return "--tts-engine piper speaks Russian only: use --to ru (or --tts-engine kokoro for --to en/ja)"
    if a.tts_engine == "piper" and not a.no_tts and not piper_onnx(a.tts_model):
        _warn_no_ru_model()  # same fallback as a missing Vosk model: subtitles only
        a.no_tts, a.ru_voice = True, False
    if a.to == "zh" and a.tts_engine != "kokoro" and not a.no_tts:
        return "--from en needs --tts-engine kokoro (Chinese voices, misaki[zh] in the Kokoro venv) or --no-tts"
    if a.tts_engine == "kokoro" and a.to not in ("en", "ja", "zh"):
        return "--tts-engine kokoro speaks English, Japanese or Chinese only: use --to en, --to ja or --from en"
    if a.tts_engine == "vosk" and a.to != "ru" and not a.no_tts:
        return "--tts-engine vosk speaks Russian only: use --to ru (or --tts-engine kokoro for --to en)"
    if a.tts_engine == "vosk" and a.to == "ru" and not a.no_tts and not _ru_model_ok(a.tts_model):
        _warn_no_ru_model()  # no other installed engine speaks Russian: fall back to subtitles only
        a.no_tts, a.ru_voice = True, False
    return None


def default_whisper(wanted: str | None, dev: str, mdir: Path) -> str:
    """#474: an explicit --whisper is used as given; otherwise large-v3-turbo on CUDA / small on CPU, falling back to
    a downloaded size like listen mode does (models.pick_whisper), so a GPU PC with only whisper-small still starts."""
    if wanted:
        return wanted
    from ..models import pick_whisper

    want = "large-v3-turbo" if dev == "cuda" else "small"
    got = pick_whisper(want, mdir, "gpu" if dev == "cuda" else "cpu")
    if got != want:
        print(f"tlwu: speech model = {got} ({want} is not downloaded: `tlwu-setup --whisper {want}`)", flush=True)
    return got


def main(argv=None) -> int:
    vm: list = []
    try:
        ap = build_parser()
        pre = ap.parse_args(argv)
        if pre.overlay:  # #322: Tk owns the main thread, speak (+listen) runs on a worker
            from ..overlay import run_listen

            if pre.mic is None:
                ap.error("--overlay needs live input (--mic)")

            return run_listen(lambda q, stop: _main(argv, vm, q, stop))
        return _main(argv, vm)
    except KeyboardInterrupt:  # second Ctrl+C during shutdown (or Ctrl+C while loading): quit quietly
        print("\nstopping...", flush=True)
        return 130
    except RuntimeError as e:
        from ..capture._soundcard import AudioDeviceLost
        if not isinstance(e, AudioDeviceLost):
            raise
        print(f"tlwu: {e}", file=sys.stderr, flush=True)  # #317: one line, no traceback
        return 2
    finally:
        for v in vm:
            try:
                v.close()
            except KeyboardInterrupt:
                pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="tinglongwu.reverse.proto")
    ap.add_argument("--input", default=None, help="dir of zh wavs (16 kHz mono) or one wav (not needed with --mic)")
    ap.add_argument("--mic", nargs="?", const="", default=None, metavar="DEVICE",
                    help="live microphone input (Chinese); optional device-name substring, default = system mic")
    ap.add_argument("--paced", action="store_true",
                    help="with --input FILE.wav: replay the file in real time through the same VAD segmenter as "
                         "--mic (the real end-of-speech wait is included); sends automatically (no confirm step)")
    ap.add_argument("--latency-jsonl", default=None, metavar="PATH",
                    help="append one JSON line per spoken line to PATH: vad, asr, merge, mt, total (end of speech -> "
                         "line shown), tts_first_audio (MT done -> first audio chunk out), total_first_audio, seconds. "
                         "--mic (with --no-confirm or --no-tts) or --input with --paced")
    ap.add_argument("--list-mics", action="store_true", help="print input device names and exit")
    ap.add_argument("--out", default=None, help="dir for synthesized wavs of --input batch runs (default: none written)")
    ap.add_argument("--save-result", "--report", dest="report", default=None, metavar="PATH",
                    help="write a JSON result (transcripts + translations) to PATH. Off by default: nothing is saved")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--whisper", default=None, help="default: large-v3-turbo on CUDA, small on CPU")
    ap.add_argument("--compute", default="auto", choices=["auto", "cpu", "cuda"],
                    help="where ASR/MT/TTS run; auto = CUDA if usable, else CPU")
    ap.add_argument("--from", dest="src", default="zh", choices=["zh", "en"],
                    help="language you speak (default zh); en = speak English, the other side hears Chinese "
                         "(--to zh implied; Kokoro Chinese voices; MT Hy-MT2 en->zh)")
    ap.add_argument("--zh-variant", choices=["tw", "cn"], default="tw",
                    help="how your Chinese is shown: tw = Traditional (OpenCC s2twp, same as listen mode), "
                         "cn = Simplified")
    ap.add_argument("--to", default=None, choices=["ru", "en", "ja", "zh", "follow"],
                    help="spoken language: follow = whatever the other side spoke last (read from --follow-file, default en; Kokoro en/ja + Vosk ru voices, started on first use); ja = Kokoro Japanese voices, zh->ja direct with Hy-MT2; ru (default) or en "
                         "(use with --tts-engine kokoro); every pair is Hy-MT2 direct")
    ap.add_argument("--mt", default="auto", type=_mt_kind, metavar="{auto,hymt}",
                    help="translator: Hy-MT2 direct (tlwu-setup --hymt), on CUDA with >= 2 GB free VRAM, else on "
                         "the CPU; auto and hymt are the same (#331: the other engines were removed)")
    ap.add_argument("--no-vmic", action="store_true",
                    help="Linux/macOS: do not create/select the virtual microphone (tlwu_mic / BlackHole 16ch); use --device yourself")
    ap.add_argument("--list-speakers", action="store_true", help="print output device names and exit")
    # #283: Qwen is gone; still accepted (ignored) because installed tlwu-talk launchers pass --qwen qwen3-4b
    ap.add_argument("--qwen", default="qwen3-4b", choices=["qwen3-4b", "qwen3-8b"], help=argparse.SUPPRESS)
    ap.add_argument("--tts-engine", default="auto", choices=["auto", "vosk", "kokoro", "piper"],
                    help="auto (default) = piper when a Piper Russian voice is given/installed "
                         "(`tlwu-setup --piper-ru`), else vosk (--to en/ja/follow need kokoro). "
                         "piper = Piper Russian voice dmitri/denis, CC0, CPU (--tts-model = its dir; GPL piper-tts "
                         "lives in the --tts-python venv); "
                         "vosk = Vosk-TTS Russian built-in voices, fast on CPU (--tts-model = vosk model dir); "
                         "kokoro = Kokoro English voices (--tts-model = dir with kokoro-v1.0.int8.onnx + "
                         "voices-v1.0.bin; --tts-python = its own venv: GPL espeak stays out of this process)")
    ap.add_argument("--speaker", type=int, default=None,
                    help="built-in voice id 0-4 (vosk ru 0.7 default 4; kokoro default 0 = am_michael, 1 af_heart, "
                         "2 am_adam, 3 af_bella, 4 bm_george); switch live with v0-v4")
    ap.add_argument("--voice", choices=("male", "female"), default=None,
                    help="#319 Russian Vosk voice by gender (ru 0.7 multi ids 0-4, upstream: 3 female, 2 male): at "
                         "startup each id speaks one test sentence, median pitch < 165 Hz = male, else female; the "
                         "lowest id of that gender is used and logged (`voice: female (speaker N)`). "
                         "--speaker N wins when both are given")
    ap.add_argument("--follow-file", default=None, metavar="PATH",
                    help="with --to follow: state file written by `tlwu --lang-file` (default: tlwu_follow_lang in "
                         "$XDG_RUNTIME_DIR or the temp dir)")
    ap.add_argument("--follow-langs", default="en,ru", metavar="LIST",
                    help="--to follow: spoken languages to prepare, subset of en,ru,ja (default en,ru)")
    ap.add_argument("--vosk-python", default="", help="--to follow: python of the Russian voice venv (Vosk or Piper)")
    ap.add_argument("--vosk-model", default="",
                    help="--to follow: Russian voice model dir, Vosk or Piper (default: installed Piper ru voice)")
    ap.add_argument("--tts-python", default="")
    ap.add_argument("--tts-model", default="")
    ap.add_argument("--end-silence", "--pause", dest="end_silence", type=float, default=None, metavar="SEC",
                    help=f"live: silence that ends a sentence ({PAUSE_MIN}-{PAUSE_MAX} s; default 0.5 on CPU + 0.1 VAD "
                         "pad, 0.6 on CUDA; env TLWU_END_SILENCE; pad: env TLWU_VAD_PAD). Lower = audio starts sooner, "
                         "but a short breath may split a sentence (try 0.3/0.5/0.8)")
    ap.add_argument("--allow-monitor", action="store_true",
                    help="Linux: accept 'Monitor of <real sound card>' as the mic (e.g. --mic \"Monitor of\": play a clip "
                         "and measure latency without speaking). Our own tlwu_ devices stay blocked")
    ap.add_argument("--max-seg", type=float, default=6.0,
                    help="force-cut a sentence after this many seconds if no pause is heard (lower = less delay)")
    ap.add_argument("--device", default=None, help='output device name substring, e.g. "CABLE Input"')
    ap.add_argument("--out-sr", type=int, default=48000)
    ap.add_argument("--no-confirm", action="store_true", help="send automatically (no confirmation step)")
    ap.add_argument("--no-back", action="store_true",
                    help="skip the back-translation shown while confirming (one MT pass less per sentence)")
    ap.add_argument("--debug-latency", action="store_true",
                    help="print one 'lat asr=.. mt=.. back=.. tts=.. total=..' line (seconds) per sentence on stderr")
    ap.add_argument("--preview-device", default=None,
                    help="also play the sent Russian audio on this device (your own headphones); default off")
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--no-tts", action="store_true", help="ASR + MT only (no TTS subprocess)")
    ap.add_argument("--overlay", action="store_true",
                    help="live (--mic): also show the subtitles in an always-on-top, draggable bar (▶ what you said, "
                         "◀ the other side with --listen); position and font size are remembered in gui.json")
    ap.add_argument("--listen", nargs=argparse.REMAINDER, default=None, metavar="TLWU_ARGS",
                    help="listen+speak in one window (#285): everything after --listen is passed to the listen side "
                         "(same options as tlwu, e.g. --listen --lang auto); it runs in this process and shares the "
                         "translation model; one Ctrl+C stops both. Must be the last option.")
    return ap


class ListenSide:
    """The `tlwu` listen pipeline in a thread of this process (#285). Its final lines go to the same console
    (◀ Them) as the speak side's (▶ Me); close() stops it. Errors on the listen side are reported and leave
    the speak side running."""

    def __init__(self, argv, run=None, parse=None, bar_q=None):
        from .. import cli

        bad = [x for x in argv if x in ("--stream", "--gui", "--report", "--probe", "--overlay")]
        if bad:
            hint = " (put --overlay before --listen: one bar shows both sides)" if bad[0] == "--overlay" else ""
            raise ValueError(f"--listen: {bad[0]} is not available next to the speak side{hint}")
        self.bar_q = bar_q
        self.args = (parse or cli.parse_args)(list(argv))
        self._run = run or cli.run_session
        self.saver = None
        if self.args.save is not None:  # #373: one transcript for both sides; opened here so ▶ lines can go in too
            from ..sink import JsonlSaveSink, default_save_path

            path = self.args.save or default_save_path()
            self.saver = JsonlSaveSink(None, path)
            self.args.save = None  # run_session must not wrap the sink a second time
            print(f"tlwu: saving final lines (both sides) to {os.path.abspath(path)}", file=sys.stderr)
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._body, name="tlwu-listen", daemon=True)

    def _body(self) -> None:
        try:
            if self.bar_q is None and self.saver is None:
                self._run(self.args, stop=self.stop)
            else:
                from ..sink import TextSink, TranscriptSink

                sink = TranscriptSink() if self.args.transcript_only else TextSink()
                if self.bar_q is not None:  # #322: the other side's lines also go to the shared bar, marked ◀
                    from ..overlay import OverlaySink

                    sink = OverlaySink(sink, self.bar_q, prefix="◀ ")
                if self.saver is not None:  # #373: heard lines -> side="them" in the shared transcript
                    self.saver.inner = sink
                    sink = self.saver
                self._run(self.args, stop=self.stop, sink=sink)
        except BaseException as e:  # noqa: BLE001 - SystemExit from a missing model too: report, keep speaking
            self.error = e
            if not self.stop.is_set():
                print(f"tlwu: listen side stopped: {describe_error(e)}", file=sys.stderr, flush=True)

    def start(self) -> "ListenSide":
        self.thread.start()
        return self

    def close(self, timeout: float = 5.0) -> None:
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout)


def _main(argv, vm, bar_q=None, stop=None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    a.bar_q, a.ext_stop = bar_q, stop  # #322 --overlay (None otherwise)
    if a.paced and (not a.input or a.mic is not None or Path(a.input).is_dir()):
        ap.error("--paced needs --input with one wav file (live --mic is always real time)")
    if a.latency_jsonl and a.mic is None and not a.paced:
        ap.error("--latency-jsonl needs --mic or --input with --paced (an unpaced file runs faster than real time)")
    if a.latency_jsonl and a.mic is not None and not (a.no_confirm or a.no_tts):
        ap.error("--latency-jsonl with --mic needs --no-confirm or --no-tts (the confirm prompt waits for you)")
    listen = None
    if a.listen is not None:  # #285: both sides in this process; split one CPU budget (threads.py)
        try:
            listen = ListenSide(a.listen, bar_q=bar_q)
        except ValueError as e:
            ap.error(str(e))
        if listen.saver is not None:
            a.me_save = listen.saver.record  # #373: my lines go to the same --save file, side="me"
        os.environ["TLWU_BOTH"] = "1"
    from .. import offline as _og

    _og.set_mode("meeting" if getattr(a, "to", "") == "follow" else "speak")
    err = check_args(a)
    if err:
        ap.error(err)
    from .confirm import setup_console

    setup_console()
    if a.list_mics:
        from .live import list_mic_names

        for n in list_mic_names():
            print(n)
        return 0
    if a.list_speakers:
        import soundcard as sc

        for sp in sc.all_speakers():
            print(sp.name)
        return 0
    from .vmic import SINK, VirtualMic, is_linux, is_macos, loop_prone_output, pick_mac_vmic, pick_win_cable

    if is_linux() and not a.no_vmic and not (a.no_play or a.no_tts) and a.device is None:
        try:
            vm.append(VirtualMic().open())
        except RuntimeError as e:
            print(f"error: could not create the virtual microphone: {e}", file=sys.stderr)
            return 2
        a.device = SINK
        print(f"virtual microphone created: select 'tlwu_mic' as the microphone in Meet/Zoom "
              f"(removed when this program exits)", flush=True)
    elif is_macos() and not a.no_vmic and not (a.no_play or a.no_tts) and a.device is None:
        try:
            import soundcard as sc

            a.device = pick_mac_vmic([sp.name for sp in sc.all_speakers()])
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"virtual microphone: speech goes to {a.device!r}; select it as the microphone in Meet/Zoom",
              flush=True)
    elif sys.platform == "win32" and not a.no_vmic and not (a.no_play or a.no_tts) and a.device is None:
        try:
            import soundcard as sc

            a.device = pick_win_cable([sp.name for sp in sc.all_speakers()])  # #311: VB-CABLE if installed
        except Exception:  # noqa: BLE001 - no device list: default speakers, warned below
            a.device = None
        if a.device:
            print(f"output: speech goes to {a.device!r}; select CABLE Output as the microphone in Meet/Zoom",
                  flush=True)
        else:
            print("warning: no 'CABLE Input' device found (VB-CABLE not installed?): the voice plays on the default "
                  "speakers and Meet/Zoom will not hear it; install VB-CABLE or pass --device", file=sys.stderr,
                  flush=True)
    if is_macos():
        for flag, val in (("--device", a.device), ("--preview-device", a.preview_device)):
            if val and loop_prone_output(val):
                print(f"error: {flag} {val!r} feeds the system-audio capture (BlackHole 2ch / Multi-Output): "
                      f"the speech would be heard by the listen side. Use your headphones or BlackHole 16ch.",
                      file=sys.stderr)
                return 2
    live = a.mic is not None or a.paced  # #434: a paced file goes through the live (mic) path
    if not live and not a.input:
        ap.error("--input is required unless --mic is given")
    a.chunk_s = a.max_seg
    if a.mic is not None:  # feedback guard BEFORE loading any model
        import soundcard as sc

        from .live import check_mic_feedback, resolve_mic_name

        dm = sc.default_microphone()
        try:
            a.mic_name = resolve_mic_name(a.mic or None, [m.name for m in sc.all_microphones(include_loopback=a.allow_monitor)],
                                          dm.name if dm else None)
            spk = None
            try:
                from .logic import match_device

                sp = sc.all_speakers()
                k = match_device([x.name for x in sp], a.device)
                spk = sp[k].name if k is not None else None
            except LookupError:
                pass
            check_mic_feedback(a.mic_name, a.device, spk, same_name_ok=is_linux(), allow_monitor=a.allow_monitor)
        except (LookupError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"microphone: {a.mic_name}", flush=True)

    import soundfile as sf

    from .. import offline  # noqa: F401  (applies offline env + network guard)
    from .. import threads as thread_budget
    from ..asr import FasterWhisperRecognizer

    thread_budget.set_role("speak")  # #250: one `tlwu: threads ...` line before any model loads
    if listen is not None:  # loads while the speak side loads; Hy-MT2 is loaded once (engines.shared_llama)
        vm.append(listen.start())
    from ..models import models_dir
    from .logic import split_sentences, translate_ru
    from .tts_client import TTSClient

    from ..device import resolve_device

    dev = resolve_device(a.compute)
    a.dev = dev
    print(f"compute device: {dev}", flush=True)
    out = Path(a.out) if a.out else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    src = Path(a.input) if a.input else None
    wavs = [] if live else (sorted(src.glob("*.wav")) if src.is_dir() else [src])
    mdir = models_dir(a.models_dir)
    a.whisper = default_whisper(a.whisper, dev, mdir)
    res: dict = {"whisper": a.whisper, "items": [],
                 "vram_mib": {"baseline": vram_used_mib()}}
    with VramSampler() as vs:
        t = time.perf_counter()
        asr = FasterWhisperRecognizer(mdir, a.whisper, a.src, dev,
                                      fast=live and os.environ.get("TLWU_CPU_FAST", "1") != "0", live=live)
        res["load_s"] = {"whisper": round(time.perf_counter() - t, 2)}
        res["vram_mib"]["after_whisper"] = vram_used_mib()
        t = time.perf_counter()
        mt = build_translator(a.mt, dev, mdir, a.to, a.follow_set, a.src)
        res["load_s"]["mt"] = round(time.perf_counter() - t, 2)
        res["vram_mib"]["after_mt"] = vram_used_mib()
        t = time.perf_counter()
        if a.to == "follow" and not a.no_tts:
            factories = {"en": lambda: TTSClient(a.tts_python, a.tts_model, "kokoro",
                                                 0 if a.speaker is None else a.speaker)}
            if getattr(a, "ru_voice", True):
                factories["ru"] = lambda: TTSClient(a.vosk_python or a.tts_python, a.vosk_model,
                                                    getattr(a, "ru_engine", "vosk"),
                                                    4 if a.speaker is None else a.speaker)
            tts = FollowTTS(factories)
            tts.client("en")  # default language up front; ru loads when the other side first speaks it
            if a.speaker is not None:
                tts.speaker = a.speaker
        else:
            tts = None if a.no_tts else TTSClient(a.tts_python, a.tts_model, a.tts_engine,
                                                  a.speaker, a.to if a.to in ("ja", "zh") else "en")
        if tts:
            res["load_s"]["tts"] = round(time.perf_counter() - t, 2)
        if tts and getattr(a, "voice", None):
            from .voice_pick import client_probe, format_table, pick_voice

            try:
                tts.speaker, table = pick_voice(a.voice, client_probe(tts))
            except (ValueError, RuntimeError, OSError) as e:  # OSError: worker died mid-probe (broken pipe)
                tts.close()
                print(f"tlwu: --voice {a.voice}: {e}", file=sys.stderr)
                return 2
            res["voice"] = {"voice": a.voice, "speaker": tts.speaker, "f0_hz": table}
            print(f"voice: {a.voice} (speaker {tts.speaker}; measured {format_table(table)})", flush=True)
        if tts:
            res["vram_mib"]["after_tts"] = vram_used_mib()
            res["tts_torch_peak_mib_after_warmup"] = tts.ready.get("vram_mib")
            warm_tts(tts, a.to)  # #207: the first real sentence must not pay the voice's lazy init
            from .logic import resample

            # #207: the player resamples every piece; importing scipy.signal there cost ~1 s on the first push
            # (measured), i.e. a gap after the first piece of the session's first utterance
            resample(np.zeros(480, np.float32), 24000, a.out_sr)
        confirm = not a.no_confirm and tts is not None and not a.paced  # #434: a file cannot answer the prompt
        player = Player(a.device, a.out_sr, enabled=not (a.no_play or a.no_tts))
        res["speaker"] = player.speaker_name
        previewer = Player(a.preview_device, a.out_sr, enabled=bool(a.preview_device) and tts is not None)
        res["confirm"] = confirm
        # warm up ASR + MT so the first sentence is not penalised by lazy init
        if wavs:
            w0, _ = sf.read(str(wavs[0]), dtype="float32")
            asr.transcribe(w0)
        if a.src == "en":
            mt.translate("Hello.", "en", "zh")
        else:
            mt.translate("你好。", "zh", "en" if a.to == "follow" else a.to)
        mt.history.clear()
        asr._last = ""
        if live:
            from ..cli import latency_recorder

            dbg = (lambda m: print(m, file=sys.stderr, flush=True)) if a.debug_latency and a.latency_jsonl else None
            a.on_record, rec_file = latency_recorder(a.latency_jsonl, dbg)  # #434 (None, None) without the flag
            try:
                _live_main(a, asr, mt, tts, player, previewer, confirm, res)
            finally:
                if rec_file is not None:
                    rec_file.close()
            wavs = []
        elif confirm:
            _run_confirm(a, _file_items(wavs, sf, asr), mt, tts, player, previewer, res)
            wavs = []
        for n, wav in enumerate(wavs, 1):
            audio, sr = sf.read(str(wav), dtype="float32")
            assert sr == 16000, f"{wav}: expected 16 kHz"
            tgt = _target(a)
            t0 = time.perf_counter()  # "end of Chinese speech": the segment is complete and handed over
            tr = asr.transcribe(audio)
            t_asr = time.perf_counter() - t0
            zh = tr.text if tr else ""
            retries = 0
            if zh:
                ru, retries = translate_ru(lambda x: mt.translate(x, a.src, tgt).text, zh, tgt)
            else:
                ru = ""
            t_mt = time.perf_counter() - t0
            item = {"file": wav.name, "audio_s": round(len(audio) / sr, 2), a.src: zh, tgt: ru,
                    "t_asr": round(t_asr, 3), "cjk_retries": retries, "t_mt": round(t_mt, 3), "tts": []}
            first_audio = None
            tts_done = t_mt
            for k, sent in enumerate([] if tts is None else split_sentences(ru), 1):
                pcm, sr_out, done = [], 24000, None
                for ev, data in tts.speak(sent):
                    if ev["t"] == "chunk":
                        if first_audio is None:
                            first_audio = time.perf_counter() - t0
                        c = np.frombuffer(data, dtype=np.float32)
                        sr_out = ev["sr"]
                        pcm.append(c)
                        player.push(c, sr_out)
                    else:
                        done = ev
                tts_done = time.perf_counter() - t0
                if pcm and out is not None:
                    sf.write(str(out / f"{wav.stem}_{tgt}{k}.wav"), np.concatenate(pcm), sr_out)
                item["tts"].append({"text": sent, **(done or {})})
            item["t_first_audio"] = round(first_audio, 3) if first_audio else None
            item["t_tts_done"] = round(tts_done, 3)
            res["items"].append(item)
            print(f"[{n}/{len(wavs)}] {wav.name} first_audio={item['t_first_audio']}s done={item['t_tts_done']}s",
                  flush=True)
        player.close()
        previewer.close()
        if tts:
            tts.close()
    for it in res["items"]:
        it.pop("_u", None)
    res["vram_mib"]["peak_sampled"] = vs.peak
    print(f"peak VRAM {vs.peak} MiB")
    if a.report:  # explicit opt-in only: the result holds transcripts and translations
        rp = Path(a.report)
        rp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
