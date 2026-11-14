"""Speak-mode command line (split from proto.py, #663): the parser and the cross-option checks."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .translator import _mt_kind


PAUSE_MIN, PAUSE_MAX = 0.3, 2.0  # silero_vad merges gaps < 300 ms (segment.py), so a shorter tail cannot be heard


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
    ap.add_argument("--send-mode", choices=("confirm", "countdown", "auto"), default="confirm",
                    help="how a line is sent (#760): confirm = y+Enter, nothing is sent until you confirm (default); "
                         "countdown = auto-send after 2 s unless you type anything+Enter; auto = send at once. "
                         "countdown and auto print a quality notice at start. "
                         "Low-confidence lines always wait for y+Enter")
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
