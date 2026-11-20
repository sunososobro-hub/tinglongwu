from __future__ import annotations

import argparse
import os
import sys
import time
from typing import TYPE_CHECKING

from . import __version__
from .capture import select_backend
from .models import ModelNotFound, models_dir
from .engines import ENGINES, engine_choice

if TYPE_CHECKING:
    from .segment import Segmenter


PRESETS = {"meeting": (8.0, 6.0), "chat": (4.0, 1.5)}  # preset -> (--chunk, --max-wait)
# #389 preset -> --min-words: a finished sentence shorter than this (words; a CJK character counts as one) is not
# translated alone but joined to a neighbour sentence, waiting at most --max-wait. chat keeps 0 (off) for latency.
PRESET_MIN_WORDS = {"meeting": 3, "chat": 0}
# #60: on CPU, ASR + MT already add seconds per line; holding an unfinished fragment the full 6 s on top makes the
# subtitle feel stuck. Used only when neither --preset nor --max-wait was given (an explicit choice is kept).
CPU_MAX_WAIT = 3.0
# #273 re-translation: with live drafts on screen a final waits for the whole sentence (the draft hides the wait), so
# the chat preset's 1.5 s cap (which cut finals mid-clause) is raised to this unless --max-wait was given. GPU only.
RETRANS_MAX_WAIT = 6.0


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="tinglongwu", description="Offline real-time speech translator.")
    p.add_argument("--version", action="version",
                   version=f"tinglongwu {__version__}; third-party model licenses: docs/THIRD_PARTY_NOTICES.md")
    p.add_argument("--file", help="translate an audio file instead of live system audio")
    p.add_argument("--mic", action="store_true", help="capture the microphone instead of loopback")
    p.add_argument("--capture-device", dest="device", help="capture device name substring")
    p.add_argument("--model", default=None,
                   help="Whisper size (default: 'small' if downloaded, else the best downloaded one)",
                   choices=["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"])
    p.add_argument("--quality", default="auto", choices=["auto", "fast", "balanced", "quality"],
                   help="speech model picked for this machine when --model is not given: auto (default) = "
                        "balanced = recommended for the detected hardware (see --probe); fast = smaller model, "
                        "lowest delay; quality = larger model, more accurate, slower. A model that is not "
                        "downloaded is replaced by the best downloaded one")
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], dest="compute",
                   help="compute device for ASR+MT: auto = cuda if usable, else cpu (default)")
    p.add_argument("--lang", default="auto", help="source language code or 'auto'")
    p.add_argument("--lang-candidates", default=None, metavar="LIST",
                   help="with --lang auto: re-detect the language of EVERY segment among these comma-separated "
                        "codes (e.g. en,ru,zh) instead of locking after the first speech. zh segments are shown "
                        "as is (no translation)")
    p.add_argument("--lang-file", default=None, metavar="PATH",
                   help="with --lang-candidates: write the last detected en/ru here (read by "
                        "`python -m tinglongwu.reverse.proto --to follow`); default off")
    p.add_argument("--to", default="zh", help="target language code (default zh)")
    p.add_argument("--face-to-face", default=None, metavar="PARTNER_LANG",
                   help="#1164: one mic, my language is --to; each utterance goes mine -> partner or partner -> mine "
                        "by its detected language")
    p.add_argument("--zh-variant", choices=["tw", "cn"], default="tw",
                   help="Chinese output: tw = Traditional (OpenCC s2twp), cn = Simplified")
    p.add_argument("--preset", choices=sorted(PRESETS), default=None,
                   help="meeting (default): --chunk 8 --max-wait 6, whole sentences, ~5-8 s latency; "
                        "chat: --chunk 4 --max-wait 1.5, lower latency, more sentence fragments. "
                        "An explicit --chunk / --max-wait overrides the preset. On CPU without --preset / "
                        f"--max-wait the default max-wait is {CPU_MAX_WAIT:g} s")
    p.add_argument("--chunk", type=float, default=None,
                   help="max segment seconds (default from --preset; larger = more context for Whisper)")
    p.add_argument("--no-splash", action="store_true",
                   help="skip the start-up mascot animation (it is also skipped when output is not a terminal, "
                        "with NO_COLOR, or on terminals without 24-bit colour)")
    p.add_argument("--selftest", action="store_true",
                   help="load the models and translate one bundled test sentence with --model/--engine/--device, "
                        "print OK/FAIL per step, fall back GPU -> CPU on failure, then exit (offline)")
    p.add_argument("--no-vad", action="store_true", help="fixed --chunk blocks instead of VAD")
    p.add_argument("--vad-pad", type=float, default=0.1, metavar="SEC",
                   help="Silero speech-end padding in seconds (default 0.1; faster-whisper's own default 0.4 delays "
                        "every subtitle cut by ~0.3 s; larger = fewer mid-sentence splits)")
    p.add_argument("--end-silence", type=float, default=None, metavar="SEC",
                   help="VAD silence in seconds that ends a segment (default 0.6, env TLWU_END_SILENCE; lower = "
                        "subtitles sooner, more mid-sentence cuts). For latency A/B runs (#21)")
    p.add_argument("--pause-send", type=float, default=None, metavar="SEC",
                   help="sentence merging: a pause of at least SEC after a segment sends an unfinished sentence at "
                        "once (default 0.8, ja 0.5, env TLWU_PAUSE_SEND). For latency A/B runs (#21)")
    p.add_argument("--models-dir", help="models directory (env TLWU_MODELS)")
    p.add_argument("--transcript-only", action="store_true",
                   help="print only source-language text, one segment per line (no translation)")
    p.add_argument("--mt-pending", action="store_true",
                   help="when the Hy-MT2 model is not installed yet (still downloading, e.g. the app's first run), "
                        "start with source-language lines and translate from the line after the model lands, "
                        "without a restart (#1357). Without it tlwu stops and prints the setup command")
    p.add_argument("--no-merge", action="store_true",
                   help="translate every VAD segment separately (default: merge into sentences first)")
    p.add_argument("--max-wait", type=float, default=None,
                   help="sentence merging: upper bound in seconds a fragment not ending in sentence punctuation "
                        "is held (default from --preset; on CPU without --preset / --max-wait: "
                        f"{CPU_MAX_WAIT:g}; a pause of >= 0.8 s sends it earlier)")
    p.add_argument("--min-words", type=int, default=None, metavar="N",
                   help="sentence merging: a finished sentence shorter than N words (a CJK character counts as a "
                        "word) is joined to the previous / next sentence instead of being translated alone, "
                        "waiting at most --max-wait (default from --preset: meeting 3, chat 0; 0 = off)")
    p.add_argument("--engine", type=engine_choice, choices=list(ENGINES), default="auto",
                   help="auto (default) = hymt: Hy-MT2 1.8B (direct src->target, `tlwu-setup --hymt`), on the GPU "
                        "when ~2 GB VRAM is free, else on the CPU; if it is missing tlwu stops and prints the setup "
                        "command (no silent engine switch, #276); hymt7b: opt-in Hy-MT2 7B for GPUs with >= 8 GB "
                        "(`tlwu-setup --hymt7b`, never picked by auto, #279); ollama: a model served by your own local Ollama (opt-in, never installed or "
                        "pulled by tlwu, #356); fm: Apple's on-device model via the macOS 27 `fm` command "
                        "(opt-in, experimental)")
    p.add_argument("--ollama-model", default=None, metavar="NAME",
                   help="with --engine ollama: model name (env TLWU_OLLAMA_MODEL; default: the first model "
                        "already installed in Ollama)")
    p.add_argument("--ollama-url", default=None, metavar="URL",
                   help="with --engine ollama: local server URL (env TLWU_OLLAMA_URL, default http://127.0.0.1:11434)")
    p.add_argument("--stream", action="store_true",
                   help="live drafts: while a segment is still being spoken, re-run Whisper on the growing "
                        "audio and translate the whole partial text at once; shows ONE dim line (the "
                        "translation) that the final line replaces (default off; needs a terminal; costs extra "
                        "GPU work; --file is paced in real time). With drafts on, a final line waits for the whole sentence: "
                        f"on GPU --max-wait is at least {RETRANS_MAX_WAIT:g} s unless given, and a timeout cuts at a "
                        "comma, not mid-clause")
    p.add_argument("--stream-src", action="store_true",
                   help="with --stream: also show the dim source-language draft line above the translation")
    p.add_argument("--debug-latency", action="store_true",
                   help="log to stderr per-stage times: ASR ms per segment, MT ms and queue wait per line; with --stream also the seconds from segment "
                        "audio start to the first translated draft and to the final line")
    p.add_argument("--paced", action="store_true",
                   help="with --file: release the audio in real time and use the live clock, like live capture "
                        "(latency measurements, meeting replay #1264); on the CPU the live ASR time budget applies "
                        "as in live capture")
    p.add_argument("--latency-jsonl", metavar="PATH",
                   help="append one JSON line per final line to PATH: seconds from the end of speech to the segment "
                        "cut (vad), ASR done (asr), sentence-merge release (merge), translation shown (mt), the "
                        "total and t_source_shown. Live capture, or --file with --paced; --debug-latency also "
                        "prints them as 'latency: line {...}'")
    p.add_argument("--no-adapt", action="store_true",
                   help="keep the chosen models even when lines arrive late (default: if speech end -> translated "
                        "line stays over 2 s for 3 lines, step down the speech model "
                        "and step back up after a stable period; one stderr line per switch)")
    p.add_argument("--builtin-glossary", action="store_true",
                   help="also load the built-in tech/dev glossary (your --glossary entries win)")
    p.add_argument("--glossary", metavar="PATH",
                   help="topic glossary: a UTF-8 .txt file or a folder of .txt files, one entry per line "
                        "`source = target` (`#` comments; a line without `=` is a plain hint term). Source terms "
                        "are given to Whisper as hotwords; entries found in the current sentence/context are "
                        "given to Hy-MT2 as reference translations; `target ~ wrong | wrong` "
                        "replaces known wrong renderings in the zh output. "
                        "Explicit flag only; offline")
    p.add_argument("--gui", action="store_true",
                   help="open the graphical interface (floating subtitle bar + small control window)")
    p.add_argument("--overlay", action="store_true",
                   help="also show the subtitles in an always-on-top, draggable bar (position and font size are "
                        "remembered in gui.json); off by default")
    p.add_argument("--voice", action="store_true",
                   help="also speak each translated line (Kokoro zh/en/ja voice, CPU, its own venv from "
                        "tlwu-setup --talk) on --voice-device, e.g. earphones; off by default. The device must not "
                        "be the one being captured or a virtual cable (it would be translated again)")
    p.add_argument("--voice-device", default=None, metavar="NAME",
                   help="with --voice: output device name substring (default: the system default output)")
    p.add_argument("--voice-speaker", type=int, default=None, metavar="N",
                   help="with --voice: Kokoro voice 0-4 of the target language (default 0)")
    p.add_argument("--tts-python", default="", help=argparse.SUPPRESS)
    p.add_argument("--tts-model", default="", help=argparse.SUPPRESS)
    p.add_argument("--save", nargs="?", const="", default=None, metavar="PATH",
                   help="append every final line to a UTF-8 JSONL file (t, wall, lang, src, dst), flushed per "
                        "line; PATH defaults to ./transcripts/<YYYYmmdd-HHMMSS>.jsonl (default off)")
    p.add_argument("--topics", default=None, metavar="MODEL.gguf",
                   help="with --save: offline per-topic notes (title, key points, action items) written to "
                        "<transcript>.topics.md by this local GGUF instruct model, in a low-priority background "
                        "thread (default off)")
    p.add_argument("--record", nargs="?", const="", default=None, metavar="DIR",
                   help="also record the audio, on this computer only: one 16 kHz WAV per final line plus "
                        "segments.jsonl (file, start, end, lang, source, translation); DIR defaults to "
                        "./transcripts/rec-<YYYYmmdd-HHMMSS> (default off; get the other side's consent first)")
    p.add_argument("--report", action="store_true",
                   help="open the browser on a pre-filled GitHub issue (version, OS, recent log); "
                        "nothing is uploaded, you review and submit it yourself")
    p.add_argument("--probe", action="store_true",
                   help="show this machine's GPU/VRAM, RAM and CPU and the recommended models, then exit (offline)")
    p.add_argument("--speed", action="store_true",
                   help="with --probe: also run the ~10 s translation speed test (CPU: Hy-MT2 on this CPU); the "
                        "result is remembered until the hardware changes")
    p.add_argument("--reprobe", action="store_true",
                   help="with --probe --speed: ignore the remembered result and measure again")
    p.add_argument("--speakers", action="store_true",
                   help="label each sentence Speaker N in the log (local sherpa-onnx; needs requirements-speaker.txt "
                        "and tlwu-setup --speaker-model). Off by default")
    p.add_argument("--force", action="store_true",
                   help="run even on a CPU without AVX2 (below the minimum spec, see README)")
    a = p.parse_args(argv)
    if a.paced and not a.file:
        p.error("--paced needs --file (live capture is always real time)")
    if a.latency_jsonl and a.file and not a.paced:
        p.error("--latency-jsonl with --file needs --paced (an unpaced file runs faster than real time)")
    if a.latency_jsonl:  # #435: fail now with a clean message, not an OSError traceback after the models load
        try:
            open(a.latency_jsonl, "a", encoding="utf-8").close()
        except OSError as e:
            p.error(f"--latency-jsonl: cannot write {a.latency_jsonl}: {e.strerror or e}")
    if a.topics is not None:  # #609
        if a.save is None:
            p.error("--topics needs --save (the notes are written next to the transcript)")
        if not os.path.isfile(a.topics):
            p.error(f"--topics: model file not found: {a.topics}")
    if a.voice and a.transcript_only:
        p.error("--voice needs translations (not --transcript-only)")
    if a.face_to_face and (a.voice or a.stream):  # #1204: TTS language and drafts are fixed to --to, not per utterance
        p.error("--face-to-face cannot be combined with --voice or --stream (the target changes per utterance)")
    if (a.voice_device is not None or a.voice_speaker is not None) and not a.voice:
        p.error("--voice-device / --voice-speaker need --voice")
    a.voice_speaker = 0 if a.voice_speaker is None else a.voice_speaker
    for opt, env in (("end_silence", "TLWU_END_SILENCE"), ("pause_send", "TLWU_PAUSE_SEND")):  # #21
        if getattr(a, opt) is None and os.environ.get(env, "").strip():
            try:
                setattr(a, opt, float(os.environ[env]))
            except ValueError:
                p.error(f"{env} must be a number of seconds, got {os.environ[env]!r}")
        if getattr(a, opt) is not None and not 0 < getattr(a, opt) <= 5:
            p.error(f"--{opt.replace('_', '-')} must be > 0 and <= 5 seconds")
    if a.end_silence is not None and not a.no_vad:  # #21: a cut reports at least end_silence + pad of silence
        from . import lang as lang_patches
        from .merge import PAUSE_SEND

        send = a.pause_send if a.pause_send is not None else (
            PAUSE_SEND if a.lang == "auto" else lang_patches.setting(a.lang, "MERGE_PAUSE_SEND", PAUSE_SEND))
        floor = a.end_silence + max(0.0, a.vad_pad)
        if floor < send:
            print(f"tlwu: warning: --end-silence {a.end_silence:g} + --vad-pad {a.vad_pad:g} < --pause-send "
                  f"{send:g}: a cut may not reach the pause that sends an unfinished sentence early, so it can "
                  f"wait for --max-wait; consider --pause-send {floor:g}", file=sys.stderr)
    a.max_wait_default = a.preset is None and a.max_wait is None  # may be lowered on CPU (cpu_max_wait)
    a.max_wait_explicit = a.max_wait is not None
    a.preset = a.preset or "meeting"
    chunk, wait = PRESETS[a.preset]
    a.chunk = chunk if a.chunk is None else a.chunk
    a.max_wait = wait if a.max_wait is None else a.max_wait
    a.min_words_explicit = a.min_words is not None  # #780: CPU mode may raise an unset value
    if a.min_words is None:
        a.min_words = PRESET_MIN_WORDS[a.preset]
    elif a.min_words < 0:
        p.error("--min-words must be >= 0")
    return a


def cpu_max_wait(a, dev: str, say=print) -> None:
    """#60: on CPU use the shorter CPU_MAX_WAIT unless the user chose --preset or --max-wait (one stderr note)."""
    if dev == "cpu" and getattr(a, "max_wait_default", False) and a.max_wait > CPU_MAX_WAIT:
        a.max_wait = CPU_MAX_WAIT
        say(f"tlwu: CPU mode: unfinished sentences are sent after at most {CPU_MAX_WAIT:g} s "
            "(--max-wait / --preset to change)", file=sys.stderr)


def want_model(level: str, dev: str, hw=None, asr_choice=None) -> tuple[str, str]:
    """(speech model, tier name) the quality level asks for on this hardware, before the "best downloaded" fallback
    of auto_model (#81, #131). #1385: the GUI uses it to say which model Accurate needs and how big its download is."""
    from dataclasses import replace

    from .hwprobe import preset, probe

    hw = hw or probe()
    real_hw = hw  # the speed-test cache is keyed by the real hardware, GPU included
    if dev == "cpu":
        hw = replace(hw, gpu=None, vram_mib=None, driver=None)
    tier = preset(level, hw)
    want = tier.asr
    if tier.name == "cpu" and level != "quality":
        if asr_choice is None:
            from .speedprobe import cpu_asr_choice as asr_choice
        from .speedprobe import smaller_asr

        want = smaller_asr(want, asr_choice(real_hw))
    return want, tier.name


def auto_model(level: str, dev: str, mdir, hw=None, say=print, asr_choice=None) -> str:
    """Speech model when --model is not given: the hardware tier's model for the quality level (#81), or the
    best downloaded one when that model is not on disk (#96). `dev` is the device really in use: on "cpu" the
    CPU tier applies even if a GPU is present (old driver, --device cpu). On the CPU tier, auto / balanced / fast
    never use a larger model than the remembered speed test found real-time on this PC (#131; `asr_choice(hw)`
    reads the cache only, never measures). quality is the user asking for the slower model, so it is kept."""
    from .models import pick_whisper

    try:
        want, tier_name = want_model(level, dev, hw, asr_choice)
    except Exception as e:  # noqa: BLE001 - a broken probe must never stop a session
        want, tier_name = "small", None
        say(f"tlwu: hardware probe failed ({str(e)[:120]}); using the default speech model", file=sys.stderr)
    got = pick_whisper(want, mdir, tier_name)
    note = "" if got == want else f" ({want} is not downloaded: `tlwu-setup --whisper {want}`)"
    say(f"tlwu: speech model = {got} (quality {level}){note}", file=sys.stderr)
    return got


def make_translator(engine: str, mdir, zh_variant: str, dev: str, say=print, choose=None,
                    ollama_model=None, ollama_url=None):
    """Pick and build the MT engine (called after Whisper is loaded so free VRAM is accurate)."""
    from .engines import choose_engine
    from .hymt import JaRouter

    name, why = (choose or choose_engine)(engine, dev, mdir)

    if name == "ollama":  # #356: opt-in only, a model the user runs in their own local Ollama
        from .ollama import OllamaTranslator

        tr = OllamaTranslator(ollama_model, ollama_url, zh_variant)
        say(f"tlwu: translation engine = ollama ({tr.model or 'first installed model'} @ {tr.url})",
            file=sys.stderr)
        return tr
    if name == "fm":  # #356: opt-in, experimental, macOS 27+ only
        from .fm import FmTranslator

        say("tlwu: translation engine = fm (Apple on-device model, experimental)", file=sys.stderr)
        return FmTranslator(zh_variant)

    if name in ("hymt2", "hymt2-cpu", "hymt2-7b"):
        from .hymt import hymt_path, setup_flag

        size = "7b" if name == "hymt2-7b" else "1.8b"
        if name == "hymt2-cpu":
            dev = "cpu"
        name = "hymt2" if size == "1.8b" else name
        flag = setup_flag(size)
        if not hymt_path(mdir, size).exists():  # #276: no fallback engine; say how to get it and stop
            raise SystemExit(f"tlwu: the translation model Hy-MT2 {size.upper()} is not installed "
                             f"({hymt_path(mdir, size)} missing). Run `tlwu-setup {flag}` once (with network), "
                             "then start again.")
        if size == "7b" and dev != "cuda":
            say("tlwu: WARNING: Hy-MT2 7B on the CPU is several times slower than 1.8B; it is meant for GPUs "
                "with >= 8 GB VRAM (#279)", file=sys.stderr)
        tr = JaRouter(None, mdir, zh_variant, dev, all_pairs=True, warn=lambda m: say(m, file=sys.stderr), size=size)
        try:
            if tr.hy is None:
                raise RuntimeError(tr.hy_error)
            tr.hy.preload()
        except Exception as e:  # noqa: BLE001 - OOM / driver / llama.cpp load errors: stop, never switch engines
            from .minspec import cpu_has_avx2

            if cpu_has_avx2() is False:  # #1014: no repair can help a CPU below the minimum spec
                raise SystemExit(f"tlwu: Hy-MT2 failed to load: this CPU lacks AVX2 (below the minimum spec, see "
                                 f"README, System requirements); {str(e)[:120]}") from e
            raise SystemExit(f"tlwu: Hy-MT2 failed to load on {dev}: {str(e)[:200]}. Repair it with "
                             f"`tlwu-setup {flag}` (and `pip install -r requirements-llama-cpu.txt` on a CPU-only "
                             "PC).") from e
        from .hymt import active_path

        on = active_path(tr)  # #1198: the backend the model really loaded on (Vulkan when dev is cpu, CPU fallback)
        say(f"tlwu: translation engine = {name} on {on[0] if on else dev}"
            + (f", {on[1]} threads" if on and on[0] == "cpu" and on[1] else "") + f" ({why})", file=sys.stderr)
        return tr
    raise SystemExit(f"tlwu: unknown translation engine {name!r}; use --engine auto (#471)")


def notes_lang(a) -> str | None:
    """#1379: the language topic notes are written in = the one the user reads (--to; zh with its variant)."""
    to = getattr(a, "to", None)
    return f"zh-{getattr(a, 'zh_variant', None) or 'tw'}" if to == "zh" else to


def pending_or_none(mdir, engine: str, build, zh_variant: str = "tw", warn=None):
    """#1357 (--mt-pending): a PendingTranslator when the default engine's Hy-MT2 1.8B model is not complete yet,
    else None (the caller builds the engine as usual). Only auto / hymt (the GUI manual engine) / hymt2 / hymt2-cpu are deferred: the other
    engines never wait for this download."""
    from .mt_pending import PendingTranslator, hymt_complete

    if engine not in ("auto", "hymt", "hymt2", "hymt2-cpu") or hymt_complete(mdir):
        return None
    return PendingTranslator(lambda: hymt_complete(mdir), build, zh_variant=zh_variant,
                             warn=warn or (lambda m: print(m, file=sys.stderr, flush=True)))


def apply_glossary(translator, glossary) -> None:
    """Hand the --glossary to the MT engine: JaRouter passes it on to Hy-MT2 (terms in its prompt, `~`
    wrong-rendering fixes on the zh output, #30, #174)."""
    if hasattr(translator, "glossary"):
        translator.glossary = glossary


def make_adaptive(a, asr, translator, mdir, dev, glossary=None, cands=None, make_asr=None, log=None):
    """Wrap ASR so the latency controller can swap speech models at run time (#80). The translation engine is
    never swapped: Hy-MT2 is the only one (#471, no OPUS step-down).
    Returns (asr, translator, controller); controller is None when there is nothing to step down to."""
    from .adapt import Ladder, LatencyController, Switchable, smaller_whisper
    from .models import whisper_installed

    ladders = []
    if make_asr is None:
        from .asr import FasterWhisperRecognizer

        def make_asr(size):
            return FasterWhisperRecognizer(mdir, size, a.lang, dev, glossary=glossary, candidates=cands,
                                           lang_file=getattr(a, "lang_file", None), live=dev == "cpu",
                                           beam=getattr(a, "cpu_beam", None),
                                           threads=getattr(a, "cpu_threads", None))
    lower = [(s, lambda s=s: make_asr(s)) for s in smaller_whisper(a.model, whisper_installed(mdir))]
    if lower:
        sp = Ladder("speech model", a.model, asr, lower)
        ladders.append(sp)
        asr = Switchable(sp)
    return asr, translator, (LatencyController(ladders, log=log) if ladders else None)


def make_draft(asr, translator, segmenter, tgt: str, stream=None, show_src: bool = False,
               debug_latency: bool = False):
    """Build the --stream draft engine, or None (with a note) when stdout is not a terminal."""
    from .stream import DraftEngine, DraftRenderer, enable_vt

    out = stream or sys.stdout
    if not getattr(out, "isatty", lambda: False)():
        print("tlwu: --stream needs an interactive terminal (stdout is redirected); drafts disabled.",
              file=sys.stderr)
        return None
    enable_vt()
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if debug_latency else None
    return DraftEngine(asr, translator, DraftRenderer(out), lambda: segmenter.live, tgt,
                       show_src=show_src, log=log)


def _hard_exit(code: int) -> None:
    """Exit now. After Ctrl+C the capture thread may sit in a blocking WASAPI/CUDA call and interpreter
    teardown (llama.cpp / CTranslate2 destructors, non-daemon native threads) can hang for a long time."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(code)


READY_LINE = "* ready - listening (start playback now)"
READY_STATUS = "listening"  # GUI status text that means "models loaded" (the GUI enables Stop on it)


class LoadProgress:
    """#332: one note per loading stage with the seconds since start, and a summary when ready, so a slow
    CPU start never looks hung. say(str) gets "loading <stage>... (<t> s)"; finish() returns the summary."""

    def __init__(self, say, clock=None):
        self.say, self.clock = say, clock or time.monotonic
        self.t0 = self.clock()
        self.cur: str | None = None
        self.cur_t = self.t0
        self.done: list[tuple[str, float]] = []

    def _close(self) -> None:
        if self.cur is not None:
            self.done.append((self.cur, self.clock() - self.cur_t))
            self.cur = None

    def stage(self, label: str) -> None:
        self._close()
        self.cur, self.cur_t = label, self.clock()
        self.say(f"loading {label}... ({self.cur_t - self.t0:.1f} s)")

    def finish(self) -> str:
        self._close()
        parts = ", ".join(f"{n} {s:.1f} s" for n, s in self.done)
        return f"loaded in {self.clock() - self.t0:.1f} s" + (f" ({parts})" if parts else "")


def announce_ready(status=None, transcript_only=False, summary: str | None = None) -> None:
    """Models are loaded: tell the user (GUI: status callback; CLI: one flushed line).
    Stdout only when it is an interactive terminal and not --transcript-only; otherwise stderr, so
    redirected stdout (hyp.txt, translations) carries only transcript/translation lines (#357).
    summary (#332): "loaded in N s (...)" from LoadProgress; GUI: appended to the status, CLI: stderr."""
    if status:
        status(f"{READY_STATUS} - {summary}" if summary else READY_STATUS)
        return
    if summary:
        print(f"tlwu: {summary}", file=sys.stderr, flush=True)
    if not transcript_only and sys.stdout.isatty():
        print(READY_LINE, flush=True)
    else:
        print(READY_LINE, file=sys.stderr, flush=True)


def listen_segmenter(a) -> Segmenter:
    """Listen-mode VAD segmenter. --end-silence / TLWU_END_SILENCE (#21) sets the silence that ends a segment
    (None = Segmenter default 0.6 s); the step never exceeds it, so a lower value is seen without extra delay."""
    from .segment import Segmenter

    sil = getattr(a, "end_silence", None)
    extra = {} if sil is None else {"min_silence": sil}
    return Segmenter(a.chunk, not a.no_vad, step=0.25 if sil is None else min(0.25, sil),
                     vad_pad=max(0.0, getattr(a, "vad_pad", 0.1)), **extra)


def apply_cpu_mode(a, dev: str, mdir, explicit_model: bool, cores=None, ram_gb=None):
    """#780: on the CPU, apply hwprobe.cpu_mode_settings. Explicit user choices win: an explicit --model is kept, an
    auto-picked model is lowered to the policy size when that is downloaded, TLWU_CPU_THREADS is kept. beam / threads
    go to the recognizer via a.cpu_beam / a.cpu_threads; the MT engine's llama.cpp threads go to threads.set_cpu_mode
    (#1198). merge=True raises an unset --min-words to the meeting value (3); an explicit --min-words wins.
    #1254: another tlwu instance already running in CPU mode keeps its threads; this one plans for what is left
    (at least instances.MIN_THREADS), registers its own and warns once with the other PIDs.
    Returns the CpuMode (None when not on the CPU)."""
    from . import threads as thread_budget

    if dev != "cpu":
        thread_budget.set_cpu_mode(None)  # a GPU run after a CPU run in the same GUI process: llama.cpp defaults
        from . import instances

        instances.release()  # #1254: no CPU threads registered for a GPU run
        return None
    from .hwprobe import cpu_mode_threads, ram_mib, cpu_mode_settings

    if ram_gb is None:
        mib = ram_mib()
        ram_gb = mib / 1024 if mib else 16.0  # unknown RAM: the middle tier
    from . import instances

    cpus = cores or thread_budget.cpu_count()  # #1264: the CPUs this process may use (affinity)
    with instances.allocating():  # #1254: two instances starting together plan one after the other
        busy = instances.others()  # other tlwu instances already on this CPU keep their threads
        n = instances.budget(cpus, busy) if busy else cpus
        st = _cpu_mode_plan(a, n, ram_gb, mdir, explicit_model, thread_budget, cpu_mode_settings, cpu_mode_threads)
        mine = a.cpu_threads + (0 if getattr(a, "transcript_only", False) else thread_budget.current()["mt"])
        instances.register(mine)  # a later instance sees these threads as taken
    if busy:
        print(instances.warning(cpus, busy, mine), file=sys.stderr, flush=True)
    return st


def _cpu_mode_plan(a, n, ram_gb, mdir, explicit_model, thread_budget, cpu_mode_settings, cpu_mode_threads):
    """apply_cpu_mode's settings for `n` CPUs (the part that runs under the #1254 allocation lock)."""
    from .models import whisper_installed

    st = cpu_mode_settings(n, ram_gb)
    plan = cpu_mode_threads(n)  # #1198: Whisper + Hy-MT2 together never ask for more threads than the CPU has
    thread_budget.set_cpu_mode(plan.mt, plan.mt_batch)
    print(f"tlwu: CPU mode threads: {n} CPUs -> asr={st.threads} mt={plan.mt}/{plan.mt_batch}", file=sys.stderr)
    order = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
    if (not explicit_model and getattr(a, "quality", "auto") == "auto" and a.model in order
            and order.index(a.model) > order.index(st.size) and st.size in whisper_installed(mdir)):
        a.model = st.size
    a.cpu_beam = st.beam
    env_threads = (os.environ.get("TLWU_CPU_THREADS") or "").strip()
    a.cpu_threads = int(env_threads) if env_threads.isdigit() and int(env_threads) > 0 else st.threads
    if st.merge and not getattr(a, "min_words_explicit", True):  # fragment merging on: at least the meeting value
        a.min_words = max(a.min_words, PRESET_MIN_WORDS["meeting"])
    return st


def cpu_fallback_mt(asked: str, got: str, cores=None, asr_threads=None, transcript_only=False) -> bool:
    """#1198: Whisper fell back from CUDA to the CPU after apply_cpu_mode ran for CUDA: give the MT engine (built
    next) the CPU-mode llama.cpp threads too. True when it did."""
    if asked == "cpu" or got != "cpu":
        return False
    from . import instances
    from . import threads as thread_budget
    from .asr import cpu_fallback_settings
    from .hwprobe import cpu_mode_threads

    cpus = cores or thread_budget.cpu_count()  # #1264: the CPUs this process may use (affinity)
    with instances.allocating():  # #1254: the fallback session is counted by (and counts) other instances
        busy = instances.others()
        plan = cpu_mode_threads(cpus)
        asr = asr_threads or cpu_fallback_settings(None, None, cores=cpus)[1] or plan.asr  # Whisper is loaded already
        mt = plan.mt if not busy else max(1, min(plan.mt, instances.budget(cpus, busy) - asr))
        thread_budget.set_cpu_mode(mt, min(plan.mt_batch, mt) if busy else plan.mt_batch)
        mine = asr + (0 if transcript_only else mt)
        instances.register(mine)
    if busy:
        print(instances.warning(cpus, busy, mine), file=sys.stderr, flush=True)
    return True


def run_session(a, sink=None, draft_factory=None, stop=None, status=None, notice=None, cpu_line=None,
                labeler_factory=None, on_topic=None, backlog_line=None, mt_wait_line=None) -> None:
    """_run_session; #1254: the CPU threads this session registered are released however it ends, also when
    loading fails before the pipeline starts (a GUI process lives on after a failed start)."""
    from . import instances

    try:
        _run_session(a, sink=sink, draft_factory=draft_factory, stop=stop, status=status, notice=notice,
                     cpu_line=cpu_line, labeler_factory=labeler_factory, on_topic=on_topic, backlog_line=backlog_line,
                     mt_wait_line=mt_wait_line)
    finally:
        instances.release()


def _run_session(a, sink=None, draft_factory=None, stop=None, status=None, notice=None, cpu_line=None,
                 labeler_factory=None, on_topic=None, backlog_line=None, mt_wait_line=None) -> None:
    """Load models and run the pipeline for parsed args `a`. Shared by the CLI and the GUI.

    sink: base sink (default: text on stdout); draft_factory(asr, translator, segmenter, tgt) -> draft engine
    or None; stop: threading.Event that ends the run; status(str): progress notes (GUI);
    notice(str): a note that should stay visible (GUI), e.g. why the GPU is not used;
    cpu_line(mt_ms, path): #780, CPU only, called once per output line with the translation time in ms and
    (#1198) path = (backend, threads) of the MT model or None (GUI status);
    backlog_line(seconds): #1259, whole seconds ASR is behind the audio (0 = caught up); default: one stderr line per change.
    mt_wait_line(lines): #1253, translations held while ASR catches up (0 = MT resumed); default: one stderr line per change.
    on_topic(topic): #611, with --topics: called on the summarizer thread with each finished topics.Topic (GUI panel)."""
    from .asr import FasterWhisperRecognizer
    from .device import resolve_device
    from .merge import AMEND_WINDOW, SentenceMerger
    from .pipeline import run
    from .i18n import t as _t
    from .sink import TextSink, TranscriptSink

    from .minspec import SlowWatch, check_avx2

    check_avx2(getattr(a, "force", False))  # #280: RuntimeError "not supported: CPU lacks AVX2" -> exit 2
    prog = LoadProgress(status or (lambda m: print(f"tlwu: {m}", file=sys.stderr, flush=True)))
    mdir = models_dir(a.models_dir)
    warned: list[str] = []

    def warn(m: str) -> None:
        print(m, file=sys.stderr)
        warned.append(m)

    dev = resolve_device(a.compute, warn=warn)
    from . import threads as thread_budget

    thread_budget.set_role("listen")  # #250: one `tlwu: threads ...` line before any model loads
    explicit_model = a.model is not None
    if a.model is None:
        a.model = auto_model(getattr(a, "quality", "auto"), dev, mdir)
    apply_cpu_mode(a, dev, mdir, explicit_model)
    if notice is not None and dev == "cpu" and a.compute != "cpu":
        from .i18n import t

        why = warned[0].removeprefix("tlwu: ") if warned else t("gpu_none")
        # #780: CPU mode shown with the reason. #1241: one line, and gpu_fallback already says "CPU mode", so the
        # gui_cpu_mode text is not appended again (the status line carries it with the per-line MT ms)
        notice(" ".join(t("gpu_fallback", why=why).split()))
    elif notice is not None and dev == "cpu":
        from .hwprobe import gpu_info  # #1664: same text as gui.core.cpu_mode_text, without importing the GUI
        from .i18n import t

        try:
            seen = gpu_info()[0] is not None
        except Exception:  # noqa: BLE001 - no probe = say nothing about the GPU
            seen = False
        # #780: --device cpu / no GPU asked for: still say CPU mode (#1525: GPU-aware wording)
        notice(t("gui_cpu_mode_gpu_unused" if seen else "gui_cpu_mode"))
    glossary = None
    if a.glossary:
        from .glossary import load as load_glossary

        try:
            glossary = load_glossary(a.glossary)
        except (OSError, UnicodeDecodeError) as e:
            raise RuntimeError(f"cannot read --glossary: {e}") from e
        print(f"tlwu: glossary = {len(glossary.entries)} entries from {a.glossary}", file=sys.stderr)
    if getattr(a, "builtin_glossary", False):
        from .glossary import load_builtin, merge

        glossary = merge(glossary, load_builtin("auto" if getattr(a, "face_to_face", None) else getattr(a, "lang", None)))
        print(f"tlwu: glossary (with built-in tech terms) = {len(glossary.entries)} entries", file=sys.stderr)
    from .glossary import with_default_hotwords

    g2 = with_default_hotwords(glossary, getattr(a, "lang", None), getattr(a, "face_to_face", None))
    if g2 is not glossary:  # #1397: e.g. Russian acronyms as Whisper hotwords
        glossary = g2
        print(f"tlwu: default {a.lang} hotwords on ({glossary.hotwords()})", file=sys.stderr)
    prog.stage("speech model")
    cands = None
    f2f_route = None
    if getattr(a, "face_to_face", None):
        from . import face_to_face as f2f

        cands = f2f.candidates(a.to, a.face_to_face)  # raises SameLanguageError when both sides match
        a.lang = "auto"
        from .langs import lang_base

        for side in (a.to, a.face_to_face):  # #1204: the zh side picks the Chinese variant (zh-cn -> cn, zh-tw -> tw)
            if side in ("zh-tw", "zh-cn"):
                a.zh_variant = side[3:]
        # #1204: MT knows "zh", not "zh-tw"/"zh-cn" (the variant is the translator's zh_variant)
        f2f_route = lambda lang: lang_base(f2f.route(a.to, a.face_to_face, lang)["target"])  # noqa: E731
        print(f"tlwu: face-to-face {a.to} <-> {a.face_to_face}", file=sys.stderr)
    elif a.lang == "auto" and getattr(a, "lang_candidates", None):
        from .langfollow import parse_candidates

        cands = parse_candidates(a.lang_candidates)
        print(f"tlwu: language follows the speaker among {','.join(cands)}", file=sys.stderr)
    asr = FasterWhisperRecognizer(mdir, a.model, a.lang, dev, glossary=glossary,
                                  candidates=cands, lang_file=getattr(a, "lang_file", None),
                                  beam=getattr(a, "cpu_beam", None), threads=getattr(a, "cpu_threads", None))
    cpu_fallback_mt(dev, asr.device, asr_threads=getattr(a, "cpu_threads", None),
                    transcript_only=bool(a.transcript_only))  # #1198: a CUDA load that fell back to the CPU gets CPU mode's MT threads
    dev = asr.device  # may have fallen back to cpu
    asr.live = asr_live(dev, a.file, getattr(a, "paced", False))  # #251 / #1264
    if stop is None or not stop.is_set():  # a session cancelled while loading does not wait for a warm-up decode
        warm_live_asr(asr, a.file, getattr(a, "paced", False), warn)  # #1247: before "ready", not on the first line
    cpu_max_wait(a, dev)
    translator = None
    if not a.transcript_only:
        prog.stage("translation engine")

        def build_mt():
            return make_translator(a.engine, mdir, a.zh_variant, dev,
                                   ollama_model=getattr(a, "ollama_model", None),
                                   ollama_url=getattr(a, "ollama_url", None))

        translator = pending_or_none(mdir, a.engine, build_mt, a.zh_variant) if getattr(a, "mt_pending", False) \
            else None
        if translator is not None:
            print(f"tlwu: {_t('mt_pending')}", file=sys.stderr, flush=True)
        else:
            translator = build_mt()
        if glossary is not None:
            apply_glossary(translator, glossary)
        if a.lang != "auto":
            translator.preload(a.lang, a.to)
        elif f2f_route is not None:  # #1204: both directions of the face-to-face pair, bases only (zh, not zh-tw)
            translator.preload(cands[0], cands[1])
            translator.preload(cands[1], cands[0])
        elif cands:  # fail early on missing models; the translator caches one step per language pair
            for c in cands:
                if c != a.to:
                    translator.preload(c, a.to)
    ctl = None
    if not getattr(a, "no_adapt", False) and not a.file:  # a file runs faster than real time: queue wait is not lag
        asr, translator, ctl = make_adaptive(a, asr, translator, mdir, dev, glossary, cands)
    rec_dir = getattr(a, "record", None)
    rec = _t("rec_label") if rec_dir is not None else ""
    if sink is None:
        sink = TranscriptSink() if a.transcript_only else TextSink()
    inner = sink  # #239: the console sink, also inside a wrapper (meeting-mode saver, overlay, voice)
    while inner is not None and not isinstance(inner, (TextSink, TranscriptSink)):
        inner = getattr(inner, "inner", None)
    if inner is not None:
        inner.rec = rec
    notes = None  # #609 --topics
    if a.save is not None:
        from .sink import JsonlSaveSink, default_save_path

        save_path = a.save or default_save_path()
        sink = JsonlSaveSink(sink, save_path)
        print(f"tlwu: saving final lines to {os.path.abspath(save_path)}", file=sys.stderr)
        if getattr(a, "topics", None):  # #609
            from . import topics

            notes = topics.attach(sink, a.topics, on_topic=on_topic, lang=notes_lang(a))
    if stop is not None and stop.is_set():
        return
    source = select_backend(a.file, a.mic, a.device)
    if notice is not None and hasattr(source, "on_silent") and getattr(a, "mic_mute", None) is None:
        # #1070: ~10 s of no sound: say what is listened to. #1544: not on the GUI speak leg (it shares the notice line)
        from .i18n import t as _t_silent

        _silent_key = getattr(source, "silent_key", "listen_silent")  # #1544: the mic has its own wording
        source.on_silent = lambda names: notice(_t_silent(_silent_key, names=" / ".join(names) or "?"))
        if hasattr(source, "on_sound"):  # the hint must not outlive the silence (0.3.8)
            from .i18n import SILENT_CLEAR

            source.on_sound = lambda: notice(SILENT_CLEAR)
    recorder = None
    if rec_dir is not None:  # #239: opt-in only (the menu asks on every start, default no)
        from .record import Recorder, default_record_dir

        recorder = Recorder(rec_dir or default_record_dir())
        print(f"tlwu: {_t('rec_consent', path=os.path.abspath(recorder.dir))}", file=sys.stderr, flush=True)
    paced = bool(a.file) and bool(getattr(a, "paced", False))  # #323: real-time file on the live clock
    audio_clock = bool(a.file) and not paced
    # #1395: a spoken / sent line cannot be taken back, so with --voice (or the GUI speak leg) a late fragment stays a
    # line of its own
    speak_leg = getattr(a, "voice", False) or getattr(a, "mic_mute", None) is not None
    merger = None if (a.no_merge or a.transcript_only) else SentenceMerger(
        a.max_wait, min_words=getattr(a, "min_words", 0), pause_send=getattr(a, "pause_send", None),
        amend_window=0.0 if speak_leg else AMEND_WINDOW)
    segmenter = listen_segmenter(a)
    draft = None
    if draft_factory is not None:
        draft = draft_factory(asr, translator, segmenter, a.to)
    elif a.stream:
        draft = make_draft(asr, translator, segmenter, a.to, show_src=a.stream_src,
                           debug_latency=a.debug_latency)
        if draft is not None and a.file and not paced:  # --stream --file: real time, audio clock (unchanged)
            from .stream import PacedSource

            source = PacedSource(source)
    if paced:
        from .stream import PacedSource

        source = PacedSource(source)
    if draft is not None and merger is not None and dev != "cpu" and not getattr(a, "max_wait_explicit", False):
        merger.max_wait = max(merger.max_wait, RETRANS_MAX_WAIT)
    labeler = None
    if getattr(a, "speakers", False):
        from .speaker_embed import make_labeler

        prog.stage("speaker model")
        labeler = make_labeler(mdir)
    elif labeler_factory is not None:  # 0.3.8 GUI: speaker ID off the subtitle path; None = unavailable, no labels
        labeler = labeler_factory(mdir)
        if labeler is not None and hasattr(labeler, "resolve"):
            w = sink
            while w is not None:  # the saved transcript gets "說話者 N", never the "spk#N" token
                if hasattr(w, "resolve_speaker"):
                    w.resolve_speaker = labeler.resolve
                w = getattr(w, "inner", None)
    lat =(lambda m: print(m, file=sys.stderr, flush=True)) if getattr(a, "debug_latency", False) else None
    on_record, rec_file = latency_recorder(getattr(a, "latency_jsonl", None), lat) if not audio_clock else (None, None)
    close_voice, interrupted = None, False
    try:
        if getattr(a, "voice", False):  # #330: opened last, so every exit path below closes it
            from .voice import VoiceUnavailable, open_voice

            prog.stage("voice")
            try:
                sink, close_voice = open_voice(a, mdir, sink, warn, off=notice)
            except VoiceUnavailable as e:  # #885: the GUI keeps listening without the voice, one plain line
                if notice is None:
                    raise
                warn(f"tlwu: {e}")
                notice(e.user)
            if getattr(sink, "capture_guard", None) is not None and hasattr(source, "on_reopen"):
                source.on_reopen = sink.capture_guard  # #710: a device change cannot loop the meeting voice
        seg_gate = None
        vgate = getattr(a, "voice_gate", None)
        gui_speak = getattr(a, "mic_mute", None) is not None  # #1070: the GUI speak leg is gated with or without voice
        if gui_speak or (getattr(a, "voice", False) and (vgate is not None or (a.mic and not a.file))):
            from .reverse.gate import segment_gate  # #968: a spoken line goes out in the user's voice

            # #1070: ASR is pinned to My language (--lang); text in another script is Whisper drifting, dropped
            seg_gate = segment_gate(getattr(vgate, "reject", None), lang=None if a.lang == "auto" else a.lang)
        muted = getattr(a, "mic_mute", None)  # #1028: the GUI mic toggle silences this leg's capture only
        playback = getattr(sink, "playback", None) if (a.mic and not a.file) else None
        if playback is not None:  # #1052: half-duplex: our own voice must not be heard by our own mic
            from .capture.base import AnyOf

            muted = AnyOf(muted, playback.busy) if muted is not None else AnyOf(playback.busy)
        if muted is not None:
            from .capture.base import MuteSource

            source = MuteSource(source, muted)
        announce_ready(status, transcript_only=bool(getattr(a, "transcript_only", False)), summary=prog.finish())
        run(source, segmenter, asr, translator, sink, a.to, merger, audio_clock=audio_clock, draft=draft,
            stop_event=stop, log=lat, on_latency=ctl.observe if ctl else None,
            speakers=labeler, on_work=cpu_on_work(None if (dev != "cpu" or a.file) else SlowWatch(say=warn).observe,
                                                  cpu_line if dev == "cpu" else None,
                                                  lambda: active_mt_path(translator)),
            on_record=on_record, recorder=recorder, seg_gate=seg_gate, route=f2f_route,
            on_backlog=backlog_line or (lambda n: print(backlog_text(n) or "tlwu: ASR caught up", file=sys.stderr, flush=True)),
            on_mt_wait=mt_wait_line or (lambda n: print(f"tlwu: {mt_wait_text(n) or 'MT resumed'}", file=sys.stderr, flush=True)),
            on_catchup=lambda n: (notice or (lambda m: print(f"tlwu: {m}", file=sys.stderr, flush=True)))(
                _t("catchup_note", lines=n)))  # #1369
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        if notes is not None:  # #609: summarize the last topic and the backlog (Ctrl+C: give up after 0 s)
            notes.close(timeout=0 if interrupted else None)
        if rec_file is not None:
            rec_file.close()
        if recorder is not None:
            recorder.close()
        if close_voice is not None:  # Ctrl+C / GUI Stop: do not keep talking through the backlog
            close_voice(drop=interrupted or (stop is not None and stop.is_set()))


def backlog_text(seconds: int) -> str:
    """#1259: the status line for an ASR backlog; "" when ASR is not behind."""
    return f"ASR {int(seconds)} s behind" if seconds > 0 else ""


def mt_wait_text(lines: int) -> str:
    """#1253: the status line while translations wait for ASR to catch up; "" when none wait."""
    return f"MT paused for ASR: {int(lines)} line(s) waiting" if lines > 0 else ""


def status_wait_text(seconds: int, lines: int) -> str:
    """#1259 + #1253: ASR backlog and held translations in one status line; "" when neither applies."""
    return " | ".join(p for p in (backlog_text(seconds), mt_wait_text(lines)) if p)


def cpu_on_work(observe, cpu_line, path=None):
    """#780: on_work callback that also reports the per-line MT time (ms) to cpu_line; either part may be None.
    path (#1198): callable -> (backend, threads) of the MT model or None; cpu_line then gets (ms, path)."""
    if cpu_line is None:
        return observe

    def on_work(asr_s, mt_s, seg_s):
        if observe is not None:
            observe(asr_s, mt_s, seg_s)
        if path is None:
            cpu_line(mt_s * 1000.0)
        else:
            cpu_line(mt_s * 1000.0, path())
    return on_work


def active_mt_path(translator):
    """#1198: (backend, threads) the Hy-MT2 model runs on, or None (other engine, not loaded, --transcript-only)."""
    from .hymt import active_path

    return active_path(translator) if translator is not None else None


def asr_live(dev: str, file, paced: bool) -> bool:
    """#251: live CPU listening caps ASR retries and time per sentence. #1264: a --paced file is live too (real
    time, live clock), so a replay drops / cuts sentences exactly where live capture would; an unpaced file runs
    faster than real time and keeps full decoding."""
    return dev == "cpu" and (not file or bool(paced))


def warm_live_asr(asr, file, paced: bool, warn=None) -> bool:
    """#1247 / #894: live listening (mic / loopback, or a --paced file) decodes once before the ready line, so the
    model's first-call setup (seconds on a GPU) does not hold back the first subtitles. An unpaced file has no live
    clock to protect. A failed warm-up is reported and the session goes on: the real decode reports a real error.
    Returns True when the warm-up ran."""
    if file and not paced:
        return False
    warm = getattr(asr, "warm_up", None)
    if not callable(warm):
        return False
    try:
        warm()
    except Exception as e:  # noqa: BLE001 - warming is an optimisation, never a reason to stop listening
        if warn is not None:
            warn(f"tlwu: speech model warm-up failed ({e})")
        return False
    return True


def latency_recorder(path, log):
    """#323: (on_record callback or None, open file or None) for --latency-jsonl PATH and/or --debug-latency."""
    if not path and log is None:
        return None, None
    import json

    fh = open(path, "a", encoding="utf-8") if path else None

    def on_record(rec: dict) -> None:
        line = json.dumps(rec, ensure_ascii=False)
        if fh is not None:
            fh.write(line + "\n")
            fh.flush()
        if log is not None:
            log("latency: line " + line)
    return on_record, fh


def main(argv=None) -> int:
    # soundcard warns on every capture glitch ("data discontinuity in recording"); harmless noise.
    # NB soundcard re-registers an "always" filter for its warning class when imported, so capture code
    # calls suppress_capture_warnings() again right after importing it.
    from .capture._soundcard import suppress_capture_warnings

    suppress_capture_warnings()
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["doctor"]:  # #313: setup check before a meeting
        from .doctor import main as doctor_main

        return doctor_main(args[1:])
    a = parse_args(argv)
    if a.report:
        from .report import open_report

        open_report(info={"Mode": "cli", "Language pair": f"{a.lang} -> {a.to}", "Engine": a.engine})
        return 0
    if a.selftest:
        from .selftest import main_from_args

        return main_from_args(a)
    if a.probe:
        from .hwprobe import describe, probe, recommend

        hw = probe()
        tier = recommend(hw)
        if a.speed or a.reprobe:
            from . import speedprobe

            r = speedprobe.run(hw, tier, models_dir(a.models_dir), force=a.reprobe)
            tier = speedprobe.apply(tier, r)
            print(describe(hw, tier))
            print(f"Speed test{' (remembered)' if r.cached else ''}: {r.note}")
            return 0
        print(describe(hw, tier))
        return 0
    from . import offline

    offline.set_mode("gui" if a.gui else "listen")
    if a.gui:
        from .gui import main as gui_main

        return gui_main()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from . import splash

    splash.play(no_splash=a.no_splash)
    if a.overlay:  # #322: Tk owns the main thread, the pipeline runs on a worker
        from .overlay import OverlaySink, run_listen
        from .sink import TextSink, TranscriptSink

        def work(q, stop):
            base = TranscriptSink() if a.transcript_only else TextSink()
            run_session(a, sink=OverlaySink(base, q), stop=stop)

        _hard_exit(run_listen(work))  # a model may still be loading: do not wait for interpreter teardown
    try:
        run_session(a)
    except (ModelNotFound, NotImplementedError, RuntimeError) as e:
        print(f"tlwu: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        _hard_exit(0)
    return 0


if __name__ == "__main__":  # `python -m tinglongwu.cli ...` behaves like `tlwu ...`
    sys.exit(main())
