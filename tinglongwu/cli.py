from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__
from .capture import select_backend
from .models import ModelNotFound, models_dir
from .engines import ENGINES


PRESETS = {"meeting": (8.0, 6.0), "chat": (4.0, 1.5)}  # preset -> (--chunk, --max-wait)
# #60: on CPU, ASR + MT already add seconds per line; holding an unfinished fragment the full 6 s on top makes the
# subtitle feel stuck. Used only when neither --preset nor --max-wait was given (an explicit choice is kept).
CPU_MAX_WAIT = 3.0
# #273 re-translation: with live drafts on screen a final waits for the whole sentence (the draft hides the wait), so
# the chat preset's 1.5 s cap (which cut finals mid-clause) is raised to this unless --max-wait was given. GPU only.
RETRANS_MAX_WAIT = 6.0


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="tinglongwu", description="Offline real-time speech translator.")
    p.add_argument("--version", action="version",
                   version=f"tinglongwu {__version__}; third-party model licenses: THIRD_PARTY_NOTICES.md")
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
    p.add_argument("--zh-variant", choices=["tw", "cn"], default="tw",
                   help="Chinese output: tw = Traditional (OpenCC s2twp), cn = Simplified")
    p.add_argument("--preset", choices=sorted(PRESETS), default=None,
                   help="meeting (default): --chunk 8 --max-wait 6, whole sentences, ~5-8 s latency; "
                        "chat: --chunk 4 --max-wait 1.5, lower latency, more sentence fragments. "
                        "An explicit --chunk / --max-wait overrides the preset. On CPU without --preset / "
                        f"--max-wait the default max-wait is {CPU_MAX_WAIT:g} s")
    p.add_argument("--chunk", type=float, default=None,
                   help="max segment seconds (default from --preset; larger = more context for Whisper)")
    p.add_argument("--selftest", action="store_true",
                   help="load the models and translate one bundled test sentence with --model/--engine/--device, "
                        "print OK/FAIL per step, fall back GPU -> CPU on failure, then exit (offline)")
    p.add_argument("--no-vad", action="store_true", help="fixed --chunk blocks instead of VAD")
    p.add_argument("--vad-pad", type=float, default=0.1, metavar="SEC",
                   help="Silero speech-end padding in seconds (default 0.1; faster-whisper's own default 0.4 delays "
                        "every subtitle cut by ~0.3 s; larger = fewer mid-sentence splits)")
    p.add_argument("--models-dir", help="models directory (env TLWU_MODELS)")
    p.add_argument("--transcript-only", action="store_true",
                   help="print only source-language text, one segment per line (no translation)")
    p.add_argument("--no-merge", action="store_true",
                   help="translate every VAD segment separately (default: merge into sentences first)")
    p.add_argument("--max-wait", type=float, default=None,
                   help="sentence merging: upper bound in seconds a fragment not ending in sentence punctuation "
                        "is held (default from --preset; on CPU without --preset / --max-wait: "
                        f"{CPU_MAX_WAIT:g}; a pause of >= 0.8 s sends it earlier)")
    p.add_argument("--engine", choices=list(ENGINES), default="auto",
                   help="auto (default) = hymt: Hy-MT2 1.8B (direct src->target, `tlwu-setup --hymt`), on the GPU "
                        "when ~2 GB VRAM is free, else on the CPU; if it is missing tlwu stops and prints the setup "
                        "command (no silent engine switch, #276); hymt7b: opt-in Hy-MT2 7B for GPUs with >= 8 GB "
                        "(`tlwu-setup --hymt7b`, never picked by auto, #279); opus: src->en->target OPUS-MT, only "
                        "when asked")
    p.add_argument("--ollama-model", default=None, metavar="NAME",
                   help="with --engine ollama: model name (env TLWU_OLLAMA_MODEL, default batiai/qwen3.5-9b:latest)")
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
                   help="log to stderr per-stage times: ASR ms per segment, MT ms (each OPUS step, e.g. "
                        "ru-en / en-zh) and queue wait per line; with --stream also the seconds from segment "
                        "audio start to the first translated draft and to the final line")
    p.add_argument("--paced", action="store_true",
                   help="with --file: release the audio in real time and use the live clock, like live capture "
                        "(for latency measurements; output text is unchanged)")
    p.add_argument("--latency-jsonl", metavar="PATH",
                   help="append one JSON line per final line to PATH: seconds from the end of speech to the segment "
                        "cut (vad), ASR done (asr), sentence-merge release (merge), translation shown (mt), the "
                        "total and t_source_shown. Live capture, or --file with --paced; --debug-latency also "
                        "prints them as 'latency: line {...}'")
    p.add_argument("--no-adapt", action="store_true",
                   help="keep the chosen models even when lines arrive late (default: if speech end -> translated "
                        "line stays over 2 s for 3 lines, step down the translation engine, then the speech "
                        "model, and step back up after a stable period; one stderr line per switch)")
    p.add_argument("--builtin-glossary", action="store_true",
                   help="also load the built-in tech/dev glossary (your --glossary entries win)")
    p.add_argument("--glossary", metavar="PATH",
                   help="topic glossary: a UTF-8 .txt file or a folder of .txt files, one entry per line "
                        "`source = target` (`#` comments; a line without `=` is a plain hint term). Source terms "
                        "are given to Whisper as hotwords; entries found in the current sentence/context are "
                        "given to Hy-MT2 as reference translations; with OPUS, `target ~ wrong | wrong` "
                        "replaces known wrong renderings in the output. "
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
    p.add_argument("--report", action="store_true",
                   help="open the browser on a pre-filled GitHub issue (version, OS, recent log); "
                        "nothing is uploaded, you review and submit it yourself")
    p.add_argument("--probe", action="store_true",
                   help="show this machine's GPU/VRAM, RAM and CPU and the recommended models, then exit (offline)")
    p.add_argument("--speed", action="store_true",
                   help="with --probe: also run the ~10 s translation speed test (CPU: Hy-MT2 vs OPUS); the "
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
    if a.voice and a.transcript_only:
        p.error("--voice needs translations (not --transcript-only)")
    if (a.voice_device is not None or a.voice_speaker is not None) and not a.voice:
        p.error("--voice-device / --voice-speaker need --voice")
    a.voice_speaker = 0 if a.voice_speaker is None else a.voice_speaker
    a.max_wait_default = a.preset is None and a.max_wait is None  # may be lowered on CPU (cpu_max_wait)
    a.max_wait_explicit = a.max_wait is not None
    a.preset = a.preset or "meeting"
    chunk, wait = PRESETS[a.preset]
    a.chunk = chunk if a.chunk is None else a.chunk
    a.max_wait = wait if a.max_wait is None else a.max_wait
    return a


def cpu_max_wait(a, dev: str, say=print) -> None:
    """#60: on CPU use the shorter CPU_MAX_WAIT unless the user chose --preset or --max-wait (one stderr note)."""
    if dev == "cpu" and getattr(a, "max_wait_default", False) and a.max_wait > CPU_MAX_WAIT:
        a.max_wait = CPU_MAX_WAIT
        say(f"tlwu: CPU mode: unfinished sentences are sent after at most {CPU_MAX_WAIT:g} s "
            "(--max-wait / --preset to change)", file=sys.stderr)


def auto_model(level: str, dev: str, mdir, hw=None, say=print, asr_choice=None) -> str:
    """Speech model when --model is not given: the hardware tier's model for the quality level (#81), or the
    best downloaded one when that model is not on disk (#96). `dev` is the device really in use: on "cpu" the
    CPU tier applies even if a GPU is present (old driver, --device cpu). On the CPU tier, auto / balanced / fast
    never use a larger model than the remembered speed test found real-time on this PC (#131; `asr_choice(hw)`
    reads the cache only, never measures). quality is the user asking for the slower model, so it is kept."""
    from dataclasses import replace

    from .hwprobe import preset, probe
    from .models import pick_whisper

    try:
        hw = hw or probe()
        real_hw = hw  # the speed-test cache is keyed by the real hardware, GPU included
        if dev == "cpu":
            hw = replace(hw, gpu=None, vram_mib=None, driver=None)
        tier = preset(level, hw)
        want, tier_name = tier.asr, tier.name
        if tier_name == "cpu" and level != "quality":
            if asr_choice is None:
                from .speedprobe import cpu_asr_choice as asr_choice
            from .speedprobe import smaller_asr

            want = smaller_asr(want, asr_choice(real_hw))
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
    from .translate import PivotTranslator

    if engine == "ollama":
        from .ollama import OllamaTranslator

        tr = OllamaTranslator(ollama_model, ollama_url, zh_variant)
        say(f"tlwu: translation engine = ollama ({tr.model} @ {tr.url})", file=sys.stderr)
        return tr
    if engine == "fm":
        from .fm import FmTranslator

        say("tlwu: translation engine = fm (Apple on-device model)", file=sys.stderr)
        return FmTranslator(zh_variant)
    name, why = (choose or choose_engine)(engine, dev, mdir)

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
        tr = JaRouter(PivotTranslator(mdir, zh_variant, device=dev), mdir, zh_variant, dev, all_pairs=True,
                      warn=lambda m: say(m, file=sys.stderr), size=size)
        try:
            if tr.hy is None:
                raise RuntimeError(tr.hy_error)
            tr.hy.preload()
        except Exception as e:  # noqa: BLE001 - OOM / driver / llama.cpp load errors: stop, never switch engines
            raise SystemExit(f"tlwu: Hy-MT2 failed to load on {dev}: {str(e)[:200]}. Repair it with "
                             f"`tlwu-setup {flag}` (and `pip install -r requirements-llama-cpu.txt` on a CPU-only "
                             "PC), or run with --engine opus.") from e
        say(f"tlwu: translation engine = {name} on {dev} ({why})", file=sys.stderr)
        return tr
    say(f"tlwu: translation engine = {name} ({why})", file=sys.stderr)

    # OPUS has no usable Japanese: ja pairs go direct through Hy-MT2 when installed, else ja -> en -> zh pivot
    return JaRouter(PivotTranslator(mdir, zh_variant, device=dev), mdir, zh_variant, dev)


def apply_glossary(translator, glossary) -> None:
    """Hand the --glossary to the MT engine: JaRouter passes it on to Hy-MT2 (terms in its prompt) and the
    OPUS pivot, which apply its `~` wrong-rendering fixes to their zh output (#30, #174)."""
    if hasattr(translator, "glossary"):
        translator.glossary = glossary


def make_adaptive(a, asr, translator, mdir, dev, glossary=None, cands=None, make_asr=None, make_opus=None,
                  log=None):
    """Wrap ASR / MT so the latency controller can swap models at run time (#80).
    Returns (asr, translator, controller); controller is None when there is nothing to step down to."""
    from .adapt import Ladder, LatencyController, Switchable, SwitchableTranslator, smaller_whisper
    from .hymt import JaRouter
    from .models import whisper_installed
    from .translate import PivotTranslator

    ladders = []
    if translator is not None and not isinstance(translator, (JaRouter, PivotTranslator)):
        def opus():
            tr = JaRouter(PivotTranslator(mdir, a.zh_variant, device=dev), mdir, a.zh_variant, dev)
            tr.glossary = glossary  # Hy-MT2 and the OPUS pivot behind the router (#174)
            for src in ([a.lang] if a.lang != "auto" else (cands or [])):
                if src != a.to:
                    tr.preload(src, a.to)  # ModelNotFound here = OPUS is not a candidate
            if not (mdir / "opus").is_dir():
                raise FileNotFoundError(mdir / "opus")
            return tr

        mt = Ladder("translation engine", getattr(translator, "name", type(translator).__name__), translator,
                    [("opus", make_opus or opus)])
        ladders.append(mt)
        translator = SwitchableTranslator(mt)
    if make_asr is None:
        from .asr import FasterWhisperRecognizer

        def make_asr(size):
            return FasterWhisperRecognizer(mdir, size, a.lang, dev, glossary=glossary, candidates=cands,
                                           lang_file=getattr(a, "lang_file", None), live=dev == "cpu")
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


def run_session(a, sink=None, draft_factory=None, stop=None, status=None, notice=None) -> None:
    """Load models and run the pipeline for parsed args `a`. Shared by the CLI and the GUI.

    sink: base sink (default: text on stdout); draft_factory(asr, translator, segmenter, tgt) -> draft engine
    or None; stop: threading.Event that ends the run; status(str): progress notes (GUI);
    notice(str): a note that should stay visible (GUI), e.g. why the GPU is not used."""
    from .asr import FasterWhisperRecognizer
    from .device import resolve_device
    from .merge import SentenceMerger
    from .pipeline import run
    from .segment import Segmenter
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
    if a.model is None:
        a.model = auto_model(getattr(a, "quality", "auto"), dev, mdir)
    if notice is not None and dev == "cpu" and a.compute != "cpu":
        from .i18n import t

        why = warned[0].removeprefix("tlwu: ") if warned else t("gpu_none")
        notice(t("gpu_fallback", why=why))
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

        glossary = merge(glossary, load_builtin())
        print(f"tlwu: glossary (with built-in tech terms) = {len(glossary.entries)} entries", file=sys.stderr)
    prog.stage("speech model")
    cands = None
    if a.lang == "auto" and getattr(a, "lang_candidates", None):
        from .langfollow import parse_candidates

        cands = parse_candidates(a.lang_candidates)
        print(f"tlwu: language follows the speaker among {','.join(cands)}", file=sys.stderr)
    asr = FasterWhisperRecognizer(mdir, a.model, a.lang, dev, glossary=glossary,
                                  candidates=cands, lang_file=getattr(a, "lang_file", None))
    dev = asr.device  # may have fallen back to cpu
    asr.live = dev == "cpu" and not a.file  # #251: live CPU listening caps ASR retries and time per sentence
    cpu_max_wait(a, dev)
    translator = None
    if not a.transcript_only:
        prog.stage("translation engine")
        translator = make_translator(a.engine, mdir, a.zh_variant, dev,
                                     ollama_model=getattr(a, "ollama_model", None),
                                     ollama_url=getattr(a, "ollama_url", None))
        if glossary is not None:
            apply_glossary(translator, glossary)
        if a.lang != "auto":
            translator.preload(a.lang, a.to)
        elif cands:  # fail early on missing models; the translator caches one step per language pair
            for c in cands:
                if c != a.to:
                    translator.preload(c, a.to)
    ctl = None
    if not getattr(a, "no_adapt", False) and not a.file:  # a file runs faster than real time: queue wait is not lag
        asr, translator, ctl = make_adaptive(a, asr, translator, mdir, dev, glossary, cands)
    if sink is None:
        sink = TranscriptSink() if a.transcript_only else TextSink()
    if a.save is not None:
        from .sink import JsonlSaveSink, default_save_path

        save_path = a.save or default_save_path()
        sink = JsonlSaveSink(sink, save_path)
        print(f"tlwu: saving final lines to {os.path.abspath(save_path)}", file=sys.stderr)
    if stop is not None and stop.is_set():
        return
    source = select_backend(a.file, a.mic, a.device)
    paced = bool(a.file) and bool(getattr(a, "paced", False))  # #323: real-time file on the live clock
    audio_clock = bool(a.file) and not paced
    merger = None if (a.no_merge or a.transcript_only) else SentenceMerger(a.max_wait)
    segmenter = Segmenter(a.chunk, not a.no_vad, step=0.25, vad_pad=max(0.0, getattr(a, "vad_pad", 0.1)))
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
    lat =(lambda m: print(m, file=sys.stderr, flush=True)) if getattr(a, "debug_latency", False) else None
    on_record, rec_file = latency_recorder(getattr(a, "latency_jsonl", None), lat) if not audio_clock else (None, None)
    close_voice, interrupted = None, False
    try:
        if getattr(a, "voice", False):  # #330: opened last, so every exit path below closes it
            from .voice import open_voice

            prog.stage("voice")
            sink, close_voice = open_voice(a, mdir, sink, warn)
        announce_ready(status, transcript_only=bool(getattr(a, "transcript_only", False)), summary=prog.finish())
        run(source, segmenter, asr, translator, sink, a.to, merger, audio_clock=audio_clock, draft=draft,
            stop_event=stop, log=lat, on_latency=ctl.observe if ctl else None,
            speakers=labeler, on_work=None if (dev != "cpu" or a.file) else SlowWatch(say=warn).observe,
            on_record=on_record)
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        if rec_file is not None:
            rec_file.close()
        if close_voice is not None:  # Ctrl+C / GUI Stop: do not keep talking through the backlog
            close_voice(drop=interrupted or (stop is not None and stop.is_set()))


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
