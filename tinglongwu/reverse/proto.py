"""Reverse-mode prototype: Chinese wav(s) -> Whisper (zh) -> Qwen3 zh->ru -> built-in-voice TTS (Vosk-TTS / Kokoro, subprocess) -> speakers + wav.

Run (main .venv):
  python -m tinglongwu.reverse.proto --input C:\\...\\reverse\\input --out C:\\...\\reverse\\out \\
      --tts-python C:\\...\\tts-venv\\Scripts\\python.exe --tts-model C:\\...\\models\\qwen3-tts-0.6b-base \\
      --ref ref_ru.wav --ref-text "..." [--qwen qwen3-8b] [--device "CABLE Input"] [--no-play]
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


class Player:
    """Plays 24 kHz mono float chunks on the chosen output device (resampled to `out_sr`) from a worker thread."""

    def __init__(self, device: str | None, out_sr: int = 48000, enabled: bool = True):
        self.out_sr, self.enabled, self.q = out_sr, enabled, queue.Queue()
        # macOS: BlackHole 16ch has 16 channels; a mono stream may reach only channel 1, so send identical L/R
        self.ch = 2 if sys.platform == "darwin" else 1
        self.speaker_name = None
        from .live import PlaybackGate

        self.gate = PlaybackGate()
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
                if item is None:
                    return
                try:
                    p.play(np.repeat(item.reshape(-1, 1), self.ch, axis=1))
                finally:
                    self.gate.end()

    def push(self, audio: np.ndarray, sr: int) -> None:
        if self.enabled:
            from .logic import resample

            self.gate.start()
            self.q.put(resample(audio, sr, self.out_sr))

    def close(self) -> None:
        if self.enabled:
            self.q.put(None)
            self.t.join(60)


def _file_items(wavs, sf, asr):
    """(n, label, zh) for each wav file with recognised speech."""
    for n, wav in enumerate(wavs, 1):
        audio, sr = sf.read(str(wav), dtype="float32")
        assert sr == 16000, f"{wav}: expected 16 kHz"
        tr = asr.transcribe(audio)
        if tr and tr.text:
            yield n, wav.name, tr.text


VOICE_ENGINES = ("vosk", "kokoro")  # engines with built-in voices (v0-v4 switch)


def _synth_sentences(tts, ru: str, stats: list | None = None):
    """Yield (audio, sr) one piece at a time, so the first piece can play while the rest is synthesized. #207: the
    pieces are speech_pieces (a short first clause, then growing pieces), not whole sentences, so the first audio
    does not wait for a long first sentence. The worker's 'done' events are appended to `stats` when given."""
    from .logic import speech_pieces

    for sent in speech_pieces(ru):
        pcm, sr_out = [], 24000
        for ev, data in tts.speak(sent):
            if ev["t"] == "chunk":
                pcm.append(np.frombuffer(data, dtype=np.float32))
                sr_out = ev["sr"]
            elif ev["t"] == "done" and stats is not None:
                stats.append(ev)
        if pcm:
            yield np.concatenate(pcm), sr_out


def _make_synth(tts):
    """Confirm-mode synth: a generator of sentence parts, run by the session's TTS worker; after "y" each part is
    sent as soon as it exists, so sentence 1 plays while the rest is still synthesized (#139)."""
    def synth(ru: str):
        return _synth_sentences(tts, ru)

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


def describe_error(e: BaseException) -> str:
    """'Type: message' (just the type when the message is empty, e.g. a bare AssertionError)."""
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


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
    show_lat = getattr(a, "debug_latency", False)
    no_back = getattr(a, "no_back", False)
    src = getattr(a, "src", "zh")  # language the user speaks (#134: en -> the other side hears zh)
    gate, pend = threading.Lock(), {}  # per-utterance: the lat dict (produce thread) and tts seconds (synth thread)

    def lat_done(u, key, val) -> None:
        """#142: tts finishes after produce() has already taken the lat dict; whichever side arrives last merges
        the two and prints/stores the complete line."""
        with gate:
            st = pend.setdefault(id(u), {})
            st[key] = val
            if "lat" not in st or "tts" not in st:
                return
            del pend[id(u)]
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
                t0 = lat.clock()
                sess.submit(u)  # synthesis starts NOW (#132): the back-translation below no longer delays it
                u.future.add_done_callback(
                    lambda f, u=u, t0=t0: None if f.cancelled() or f.exception() else lat_done(u, "tts", lat.clock() - t0))
                if not no_back:
                    sess.set_back(u, lat.timed("back", back_translate, mt, ru, lang, src))
                u.extra["lat"] = taken = lat.take()  # partial until tts lands (lat_done merges it)
                lat_done(u, "lat", taken)
        except Exception as e:  # noqa: BLE001
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


def _run_auto_live(items, mt, tts, player, res, lang="ru", lat=None, show_lat=False, src="zh") -> None:
    """--no-confirm with live input: translate + speak every utterance as it arrives. Each sentence goes to the
    player as soon as it is synthesized (#132: the first sentence plays while the rest is still in TTS).
    `lang`: a code, or a callable returning the code for the next sentence (--to follow)."""
    from .logic import GapMeter, Lat, lat_line, latency_fields, latency_line, stream_line, timing_line, translate_ru

    lat = lat or Lat()
    for n, label, zh in items:
        lg = lang() if callable(lang) else lang
        ru, _ = lat.timed("mt", translate_ru, lambda x: mt.translate(x, src, lg).text, zh, lg)
        print("\n" + fmt.block("me", zh, [(None, ru)], color=fmt.color_enabled(sys.stdout)).rstrip("\n"), flush=True)
        t0, sent = lat.clock(), 0
        gaps, stats = GapMeter(lat.clock), []
        for audio, sr in _synth_sentences(tts, ru, stats):
            if not sent:
                lat.set("tts", lat.clock() - t0)
                print(timing_line(lat.t), flush=True)  # #186: first audio is out; one line under the translation
            gaps.push(len(audio) / sr)
            player.push(audio, sr)
            sent += 1
        tts_done = lat.clock() - t0
        if sent:  # #207: how the streamed pieces kept up with playback
            print(stream_line(sent, gaps.max, stats), file=sys.stderr, flush=True)
        t = lat.take()
        # #180: one batch-style line per utterance (same fields as the --report items), always on
        f = latency_fields(t.get("asr", 0.0), t.get("mt", 0.0), t.get("tts") if sent else None, tts_done)
        print(latency_line(f), file=sys.stderr, flush=True)
        if show_lat:
            print(lat_line(t), file=sys.stderr, flush=True)
        res["items"].append({"file": label, src: zh, lg: ru, "decision": "sent" if sent else "no_audio", "lat": t, **f,
                             "pieces": sent, "gap_max": gaps.max})


PAUSE_MIN, PAUSE_MAX = 0.3, 2.0  # silero_vad merges gaps < 300 ms (segment.py), so a shorter tail cannot be heard


def make_segmenter(max_seg: float, dev: str = "cpu", pause=None, vad_fn=None, env=None):
    """Live speak-mode VAD segmenter: an utterance ends after `pause` s of silence (--pause/--end-silence, #139;
    None = live_endpoint's per-device default). Returns (segmenter, configured silence)."""
    from ..segment import Segmenter
    from .logic import live_endpoint, live_vad_pad

    sil, step = live_endpoint(dev, pause, env)
    extra = {"vad_fn": vad_fn} if vad_fn is not None else {}
    return Segmenter(max_seg, True, min_silence=sil, step=step, vad_pad=live_vad_pad(dev, env), **extra), sil


def _live_main(a, asr, mt, tts, player, previewer, confirm, res) -> None:
    """Mic -> VAD segments -> Whisper(zh) -> same pipeline. Ctrl+C stops capture and kills the TTS subprocess."""
    from ..capture.mic import MicSource
    from .live import capture_segments, recognize_utterances

    from .logic import Lat, TimedASR, timing_line

    stop = threading.Event()
    src = getattr(a, "src", "zh")
    seg, sil = make_segmenter(a.chunk_s, getattr(a, "dev", "cpu"), getattr(a, "end_silence", None))
    a.lat = lat = Lat(fixed={"vad": sil})  # configured end-of-speech silence, shown as vad= in the lat line (#139)
    asr = TimedASR(asr, lat) if asr is not None else None
    items = ((n, "mic", zh) for n, zh in enumerate(
        recognize_utterances(capture_segments(MicSource(a.mic_name), seg, stop, lat), asr,
                         gate=getattr(player, "gate", None), src=src), 1))
    box: list = []
    print(f"listening... speak {'English' if src == 'en' else 'Chinese'}; Ctrl+C to quit", flush=True)
    try:
        if tts is None:  # text only: show zh -> ru, nothing is spoken
            from .logic import translate_ru

            for _n, _src, zh in items:
                lang = _target(a)
                ru, _r = lat.timed("mt", translate_ru, lambda x: mt.translate(x, src, lang).text, zh, lang)
                print(fmt.block("me", zh, [(None, ru)], color=fmt.color_enabled(sys.stdout)), end="", flush=True)
                print(timing_line(lat.take(), tts=False), flush=True)  # #186
        elif confirm:
            _run_confirm(a, items, mt, tts, player, previewer, res, box)
        else:
            _run_auto_live(items, mt, tts, player, res, lambda: _target(a), lat,
                           getattr(a, "debug_latency", False), src)
    except KeyboardInterrupt:
        print("\nstopping...", flush=True)
    finally:
        stop.set()
        for sess in box:
            sess.abort()
        if tts is not None:
            try:
                tts.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        player.enabled = False
        previewer.enabled = False


def choose_speak_mt(kind: str, device: str, mdir, free_mib=None) -> tuple[str, str]:
    """(engine, reason) for speak mode; engine in hymt | qwen | opus (#139).

    auto = Hy-MT2 on CUDA when its GGUF is installed and free VRAM >= qwen.HYMT_MIN_FREE_MIB (the listen-mode rule,
    qwen.choose_engine / #66), else OPUS. Qwen is used only when asked for (--mt qwen): Hy-MT2 matches Qwen3-8B
    quality at ~4x the speed and ~1.5 GB VRAM, and speak mode is latency-bound."""
    if kind != "auto":
        return kind, "requested"
    if device != "cuda":
        return "opus", "no usable CUDA GPU (CPU mode)"
    from ..hymt import hymt_path
    from ..qwen import HYMT_MIN_FREE_MIB, free_vram_mib

    if not hymt_path(mdir).exists():
        print(f"Hy-MT2 (the recommended GPU translation engine) is not installed ({hymt_path(mdir)} missing); "
              f"run `tlwu-setup --hymt` once (with network). Using OPUS; --mt qwen for Qwen3.", file=sys.stderr)
        return "opus", "Hy-MT2 not installed"
    free = (free_mib or free_vram_mib)()
    if free is None:
        return "opus", "could not read free VRAM"
    if free < HYMT_MIN_FREE_MIB:
        return "opus", f"{free} MiB VRAM free < {HYMT_MIN_FREE_MIB} MiB for Hy-MT2"
    return "hymt", f"{free} MiB VRAM free >= {HYMT_MIN_FREE_MIB} MiB"


def build_translator(kind: str, device: str, mdir, qwen: str, to: str = "ru", follow_langs=("en", "ru"),
                     src: str = "zh", free_mib=None, hy_factory=None):
    """zh->ru|en|ja translator (and the reverse, for the back-translation shown while confirming).
    hymt = Hy-MT2 direct for every pair (`tlwu-setup --hymt`; falls back to OPUS loudly if it fails to load);
    qwen (GPU, any target); opus (Argos/OPUS on CPU): ru = pivot zh->en->ru, needs
    `tlwu-setup --pair zh-en --pair en-ru --pair ru-en --pair en-zh`; en = direct zh->en, needs
    `tlwu-setup --pair zh-en --pair en-zh` (en-zh only for the back-translation shown while confirming);
    ja = direct zh<->ja through Hy-MT2 (OPUS has no usable Japanese), the rest stays OPUS.
    auto = see choose_speak_mt (Hy-MT2 on CUDA when installed, else OPUS).
    src en (#134, to zh): auto/opus = Hy-MT2 direct en<->zh when installed, else the OPUS en-zh / zh-en pairs;
    qwen only when asked for (--mt qwen)."""
    if src == "en" and kind == "auto":
        kind, why = "opus", "--from en: Hy-MT2 en<->zh when installed, else OPUS"
    else:
        kind, why = choose_speak_mt(kind, device, mdir, free_mib)
    print(f"translation engine: {kind} ({why})", flush=True)
    if kind == "qwen":
        from ..qwen import QwenTranslator

        mt = QwenTranslator(mdir, qwen, "tw", device)
        mt.preload()
        return mt
    from ..hymt import JaRouter
    from ..translate import PivotTranslator

    targets = tuple(follow_langs) if to == "follow" else (to,)
    dev = "cpu" if device != "cuda" else "cuda"
    mt = PivotTranslator(mdir, "tw", device=dev)
    if kind == "hymt":
        from ..hymt import hymt_path
        from ..models import require

        require(hymt_path(mdir), "Hy-MT2 translation model", "--hymt")  # explicit --mt hymt without the file
        mt = JaRouter(mt, mdir, "tw", dev, all_pairs=True, **({"factory": hy_factory} if hy_factory else {}))
        if mt.hy is None:
            mt._fallback(RuntimeError(mt.hy_error))
    elif src != "zh":
        mt = JaRouter(mt, mdir, "tw", dev, all_pairs=True)
    elif "ja" in targets:
        mt = JaRouter(mt, mdir, "tw", dev)
    for t in targets:  # follow: every spoken language
        mt.preload(src, t)  # fail early if the models are missing
        mt.preload(t, src)  # back-translation for the confirm step
    mt.history, mt._pair = [], None  # same surface as QwenTranslator for confirm.back_translate
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
    from ..models import default_piper_ru, piper_onnx

    mdir = getattr(a, "models_dir", None)
    if a.to == "follow":
        if not a.vosk_model and (d := default_piper_ru(mdir)):
            a.vosk_model = str(d)
        a.ru_engine = "piper" if piper_onnx(a.vosk_model) else "vosk"
        return
    if a.tts_engine != "auto":
        return
    if a.to != "ru":
        a.tts_engine = "vosk"  # unchanged default: rejected below unless --no-tts
    elif piper_onnx(a.tts_model):
        a.tts_engine = "piper"
    elif not a.tts_model and (d := default_piper_ru(mdir)):
        a.tts_engine, a.tts_model = "piper", str(d)
    else:
        a.tts_engine = "vosk"


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


def main(argv=None) -> int:
    vm: list = []
    try:
        return _main(argv, vm)
    except KeyboardInterrupt:  # second Ctrl+C during shutdown (or Ctrl+C while loading): quit quietly
        print("\nstopping...", flush=True)
        return 130
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
                         "(--to zh implied; Kokoro Chinese voices; MT Hy-MT2 en->zh if installed, else OPUS)")
    ap.add_argument("--to", default=None, choices=["ru", "en", "ja", "zh", "follow"],
                    help="spoken language: follow = whatever the other side spoke last (read from --follow-file, default en; Kokoro en/ja + Vosk ru voices, started on first use); ja = Kokoro Japanese voices, zh->ja direct with Hy-MT2; ru (default; OPUS = pivot zh->en->ru) or en (OPUS = direct zh->en, "
                         "no pivot; use with --tts-engine kokoro)")
    ap.add_argument("--mt", default="auto", choices=["auto", "hymt", "qwen", "opus"],
                    help="translator; auto = Hy-MT2 on CUDA when installed (tlwu-setup --hymt), else OPUS/Argos "
                         "(ru: pivot zh->en->ru; en: direct zh->en); hymt = Hy-MT2 direct; qwen = Qwen3 (--qwen)")
    ap.add_argument("--no-vmic", action="store_true",
                    help="Linux/macOS: do not create/select the virtual microphone (tlwu_mic / BlackHole 16ch); use --device yourself")
    ap.add_argument("--list-speakers", action="store_true", help="print output device names and exit")
    ap.add_argument("--qwen", default="qwen3-4b", choices=["qwen3-4b", "qwen3-8b"])
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
    return ap


def _main(argv, vm) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
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
    from .vmic import SINK, VirtualMic, is_linux, is_macos, loop_prone_output, pick_mac_vmic

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
    if is_macos():
        for flag, val in (("--device", a.device), ("--preview-device", a.preview_device)):
            if val and loop_prone_output(val):
                print(f"error: {flag} {val!r} feeds the system-audio capture (BlackHole 2ch / Multi-Output): "
                      f"the speech would be heard by the listen side. Use your headphones or BlackHole 16ch.",
                      file=sys.stderr)
                return 2
    live = a.mic is not None
    if not live and not a.input:
        ap.error("--input is required unless --mic is given")
    a.chunk_s = a.max_seg
    if live:  # feedback guard BEFORE loading any model
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
    from ..asr import FasterWhisperRecognizer
    from ..models import models_dir
    from .logic import split_sentences, translate_ru
    from .tts_client import TTSClient

    from ..device import resolve_device

    dev = resolve_device(a.compute)
    a.whisper = a.whisper or ("large-v3-turbo" if dev == "cuda" else "small")
    a.dev = dev
    print(f"compute device: {dev}", flush=True)
    out = Path(a.out) if a.out else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    src = Path(a.input) if a.input else None
    wavs = [] if live else (sorted(src.glob("*.wav")) if src.is_dir() else [src])
    mdir = models_dir(a.models_dir)
    res: dict = {"qwen": a.qwen, "whisper": a.whisper, "items": [],
                 "vram_mib": {"baseline": vram_used_mib()}}
    with VramSampler() as vs:
        t = time.perf_counter()
        asr = FasterWhisperRecognizer(mdir, a.whisper, a.src, dev,
                                      fast=live and os.environ.get("TLWU_CPU_FAST", "1") != "0")
        res["load_s"] = {"whisper": round(time.perf_counter() - t, 2)}
        res["vram_mib"]["after_whisper"] = vram_used_mib()
        t = time.perf_counter()
        mt = build_translator(a.mt, dev, mdir, a.qwen, a.to, a.follow_set, a.src)
        res["load_s"]["qwen"] = round(time.perf_counter() - t, 2)
        res["vram_mib"]["after_qwen"] = vram_used_mib()
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
            res["vram_mib"]["after_tts"] = vram_used_mib()
            res["tts_torch_peak_mib_after_warmup"] = tts.ready.get("vram_mib")
        confirm = not a.no_confirm and tts is not None
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
            _live_main(a, asr, mt, tts, player, previewer, confirm, res)
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
