from __future__ import annotations

import argparse
import os
import sys

from .capture import select_backend
from .models import ModelNotFound, models_dir
from .qwen import ENGINES


PRESETS = {"meeting": (8.0, 6.0), "chat": (4.0, 1.5)}  # preset -> (--chunk, --max-wait)
# #60: on CPU, ASR + MT already add seconds per line; holding an unfinished fragment the full 6 s on top makes the
# subtitle feel stuck. Used only when neither --preset nor --max-wait was given (an explicit choice is kept).
CPU_MAX_WAIT = 3.0
# #273 re-translation: with live drafts on screen a final waits for the whole sentence (the draft hides the wait), so
# the chat preset's 1.5 s cap (which cut finals mid-clause) is raised to this unless --max-wait was given. GPU only.
RETRANS_MAX_WAIT = 6.0


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="tinglongwu", description="Offline real-time speech translator.")
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
    p.add_argument("--no-vad", action="store_true", help="fixed --chunk blocks instead of VAD")
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
                        "command (no silent engine switch, #276); opus: src->en->target OPUS-MT, only when asked")
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
                        "given to the Qwen engine as required translations; with OPUS, `target ~ wrong | wrong` "
                        "replaces known wrong renderings in the output. "
                        "Explicit flag only; offline")
    p.add_argument("--gui", action="store_true",
                   help="open the graphical interface (floating subtitle bar + small control window)")
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
    a = p.parse_args(argv)
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


def make_translator(engine: str, mdir, zh_variant: str, dev: str, say=print, choose=None):
    """Pick and build the MT engine (called after Whisper is loaded so free VRAM is accurate)."""
    from .qwen import build_qwen_with_fallback, choose_engine
    from .translate import PivotTranslator

    name, why = (choose or choose_engine)(engine, dev, mdir)
    if name in ("qwen3-8b", "qwen3-4b"):
        errors: list = []
        tr, got = build_qwen_with_fallback(mdir, name, zh_variant, dev, errors=errors)
        if tr is not None:
            say(f"tlwu: translation engine = {got} ({why})" if got == name
                else f"tlwu: translation engine = {got} ({name} failed to load, fell back)", file=sys.stderr)
            return tr
        # loud on purpose (#74): OPUS translates much worse, the user must know it is not Qwen and why
        say(f"tlwu: WARNING: Qwen engine failed to load, falling back to OPUS (lower translation quality). "
            f"Reason: {errors[-1] if errors else 'unknown'}", file=sys.stderr)
        name, why = "opus", "Qwen failed to load"
    from .hymt import JaRouter

    if name in ("hymt2", "hymt2-cpu"):
        from .hymt import hymt_path

        if name == "hymt2-cpu":
            name, dev = "hymt2", "cpu"
        if not hymt_path(mdir).exists():  # #276: no fallback engine; say how to get it and stop
            raise SystemExit(f"tlwu: the translation model Hy-MT2 is not installed ({hymt_path(mdir)} missing). "
                             "Run `tlwu-setup --hymt` once (with network), then start again.")
        tr = JaRouter(PivotTranslator(mdir, zh_variant, device=dev), mdir, zh_variant, dev, all_pairs=True,
                      warn=lambda m: say(m, file=sys.stderr))
        try:
            if tr.hy is None:
                raise RuntimeError(tr.hy_error)
            tr.hy.preload()
        except Exception as e:  # noqa: BLE001 - OOM / driver / llama.cpp load errors: stop, never switch engines
            raise SystemExit(f"tlwu: Hy-MT2 failed to load on {dev}: {str(e)[:200]}. Repair it with "
                             "`tlwu-setup --hymt` (and `pip install -r requirements-llama-cpu.txt` on a CPU-only "
                             "PC), or run with --engine opus.") from e
        say(f"tlwu: translation engine = hymt2 on {dev} ({why})", file=sys.stderr)
        return tr
    say(f"tlwu: translation engine = {name} ({why})", file=sys.stderr)

    # OPUS has no usable Japanese: ja pairs go direct through Hy-MT2 when installed, else ja -> en -> zh pivot
    return JaRouter(PivotTranslator(mdir, zh_variant, device=dev), mdir, zh_variant, dev)


def apply_glossary(translator, glossary) -> None:
    """Hand the --glossary to the MT engine: Qwen uses it in its prompt; JaRouter passes it on to Hy-MT2 and the
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
                                           lang_file=getattr(a, "lang_file", None))
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

    note = status or (lambda m: None)
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
    note("loading speech model...")
    cands = None
    if a.lang == "auto" and getattr(a, "lang_candidates", None):
        from .langfollow import parse_candidates

        cands = parse_candidates(a.lang_candidates)
        print(f"tlwu: language follows the speaker among {','.join(cands)}", file=sys.stderr)
    asr = FasterWhisperRecognizer(mdir, a.model, a.lang, dev, glossary=glossary,
                                  candidates=cands, lang_file=getattr(a, "lang_file", None))
    dev = asr.device  # may have fallen back to cpu
    cpu_max_wait(a, dev)
    translator = None
    if not a.transcript_only:
        note("loading translation engine...")
        translator = make_translator(a.engine, mdir, a.zh_variant, dev)
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
    merger = None if (a.no_merge or a.transcript_only) else SentenceMerger(a.max_wait)
    segmenter = Segmenter(a.chunk, not a.no_vad)
    draft = None
    if draft_factory is not None:
        draft = draft_factory(asr, translator, segmenter, a.to)
    elif a.stream:
        draft = make_draft(asr, translator, segmenter, a.to, show_src=a.stream_src,
                           debug_latency=a.debug_latency)
        if draft is not None and a.file:
            from .stream import PacedSource

            source = PacedSource(source)
    if draft is not None and merger is not None and dev != "cpu" and not getattr(a, "max_wait_explicit", False):
        merger.max_wait = max(merger.max_wait, RETRANS_MAX_WAIT)
    labeler = None
    if getattr(a, "speakers", False):
        from .speaker_embed import make_labeler

        note("loading speaker model...")
        labeler = make_labeler(mdir)
    note("listening")
    lat =(lambda m: print(m, file=sys.stderr, flush=True)) if getattr(a, "debug_latency", False) else None
    run(source, segmenter, asr, translator, sink, a.to, merger, audio_clock=bool(a.file), draft=draft,
        stop_event=stop, log=lat, on_latency=ctl.observe if ctl else None,
        speakers=labeler)


def main(argv=None) -> int:
    # soundcard warns on every capture glitch ("data discontinuity in recording"); harmless noise.
    # NB soundcard re-registers an "always" filter for its warning class when imported, so capture code
    # calls suppress_capture_warnings() again right after importing it.
    from .capture._soundcard import suppress_capture_warnings

    suppress_capture_warnings()
    a = parse_args(argv)
    if a.report:
        from .report import open_report

        open_report(info={"Mode": "cli", "Language pair": f"{a.lang} -> {a.to}", "Engine": a.engine})
        return 0
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
    try:
        run_session(a)
    except (ModelNotFound, NotImplementedError, RuntimeError) as e:
        print(f"tlwu: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        _hard_exit(0)
    return 0
