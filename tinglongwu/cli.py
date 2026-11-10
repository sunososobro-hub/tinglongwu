from __future__ import annotations

import argparse
import os
import sys

from .capture import select_backend
from .models import ModelNotFound, models_dir


PRESETS = {"meeting": (8.0, 6.0), "chat": (4.0, 1.5)}  # preset -> (--chunk, --max-wait)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="tinglongwu", description="Offline real-time speech translator.")
    p.add_argument("--file", help="translate an audio file instead of live system audio")
    p.add_argument("--mic", action="store_true", help="capture the microphone instead of loopback")
    p.add_argument("--capture-device", dest="device", help="capture device name substring")
    p.add_argument("--model", default="small",
                   choices=["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"])
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
    p.add_argument("--preset", choices=sorted(PRESETS), default="meeting",
                   help="meeting (default): --chunk 8 --max-wait 6, whole sentences, ~5-8 s latency; "
                        "chat: --chunk 4 --max-wait 1.5, lower latency, more sentence fragments. "
                        "An explicit --chunk / --max-wait overrides the preset")
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
                        "is held (default from --preset; a pause of >= 0.8 s sends it earlier)")
    p.add_argument("--engine", choices=["auto", "qwen", "qwen4b", "opus", "m2m"], default="auto",
                   help="auto (default): after Whisper loads, pick by free VRAM: Qwen3-8B (>=5.8 GB free), "
                        "else Qwen3-4B (>=3.5 GB), else OPUS (also on CPU); qwen = Qwen3-8B, qwen4b = Qwen3-4B "
                        "(direct src->target LLM, needs `tlwu-setup --qwen`); opus: src->en->target OPUS-MT; "
                        "m2m: M2M-100 1.2B direct")
    p.add_argument("--stream", action="store_true",
                   help="live drafts: while a segment is still being spoken, re-run Whisper on the growing "
                        "audio and translate the whole partial text at once; shows ONE dim line (the "
                        "translation) that the final line replaces (default off; needs a terminal; costs extra "
                        "GPU work; --file is paced in real time)")
    p.add_argument("--stream-src", action="store_true",
                   help="with --stream: also show the dim source-language draft line above the translation")
    p.add_argument("--debug-latency", action="store_true",
                   help="with --stream: log to stderr the seconds from segment audio start to the first "
                        "translated draft and to the final line")
    p.add_argument("--glossary", metavar="PATH",
                   help="topic glossary: a UTF-8 .txt file or a folder of .txt files, one entry per line "
                        "`source = target` (`#` comments; a line without `=` is a plain hint term). Source terms "
                        "are given to Whisper as hotwords; entries found in the current sentence/context are "
                        "given to the Qwen engine as required translations (OPUS/M2M ignore targets). "
                        "Explicit flag only; offline")
    p.add_argument("--gui", action="store_true",
                   help="open the graphical interface (floating subtitle bar + small control window)")
    p.add_argument("--save", nargs="?", const="", default=None, metavar="PATH",
                   help="append every final line to a UTF-8 JSONL file (t, wall, lang, src, dst), flushed per "
                        "line; PATH defaults to ./transcripts/<YYYYmmdd-HHMMSS>.jsonl (default off)")
    a = p.parse_args(argv)
    chunk, wait = PRESETS[a.preset]
    a.chunk = chunk if a.chunk is None else a.chunk
    a.max_wait = wait if a.max_wait is None else a.max_wait
    return a


def make_translator(engine: str, mdir, zh_variant: str, dev: str, say=print, choose=None):
    """Pick and build the MT engine (called after Whisper is loaded so free VRAM is accurate)."""
    from .qwen import build_qwen_with_fallback, choose_engine
    from .translate import M2mTranslator, PivotTranslator

    name, why = (choose or choose_engine)(engine, dev, mdir)
    if name in ("qwen3-8b", "qwen3-4b"):
        tr, got = build_qwen_with_fallback(mdir, name, zh_variant, dev)
        if tr is not None:
            say(f"tlwu: translation engine = {got} ({why})" if got == name
                else f"tlwu: translation engine = {got} ({name} failed to load, fell back)", file=sys.stderr)
            return tr
        name, why = "opus", "Qwen failed to load"
    say(f"tlwu: translation engine = {name} ({why})", file=sys.stderr)
    if name == "m2m":
        return M2mTranslator(mdir, zh_variant, dev)
    from .hymt import JaRouter

    # OPUS has no usable Japanese: ja pairs go direct through Hy-MT2 when installed, else ja -> en -> zh pivot
    return JaRouter(PivotTranslator(mdir, zh_variant, device=dev), mdir, zh_variant, dev)


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
    if notice is not None and dev == "cpu" and a.compute != "cpu":
        why = warned[0] if warned else "沒有偵測到可用的 NVIDIA 顯示卡。"
        notice(f"GPU 無法使用，已自動改用 CPU 模式（翻譯會比較慢）。\n原因：{why}")
    glossary = None
    if a.glossary:
        from .glossary import load as load_glossary

        try:
            glossary = load_glossary(a.glossary)
        except (OSError, UnicodeDecodeError) as e:
            raise RuntimeError(f"cannot read --glossary: {e}") from e
        print(f"tlwu: glossary = {len(glossary.entries)} entries from {a.glossary}", file=sys.stderr)
    note("loading speech model...")
    cands = None
    if a.lang == "auto" and getattr(a, "lang_candidates", None):
        from .langfollow import parse_candidates

        cands = parse_candidates(a.lang_candidates)
        print(f"tlwu: language follows the speaker among {','.join(cands)}", file=sys.stderr)
    asr = FasterWhisperRecognizer(mdir, a.model, a.lang, dev, glossary=glossary,
                                  candidates=cands, lang_file=getattr(a, "lang_file", None))
    dev = asr.device  # may have fallen back to cpu
    translator = None
    if not a.transcript_only:
        note("loading translation engine...")
        translator = make_translator(a.engine, mdir, a.zh_variant, dev)
        if glossary is not None and hasattr(translator, "glossary"):
            translator.glossary = glossary
        if a.lang != "auto":
            translator.preload(a.lang, a.to)
        elif cands:  # fail early on missing models; the translator caches one step per language pair
            for c in cands:
                if c != a.to:
                    translator.preload(c, a.to)
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
    note("listening")
    run(source, segmenter, asr, translator, sink, a.to, merger, audio_clock=bool(a.file), draft=draft,
        stop_event=stop)


def main(argv=None) -> int:
    # soundcard warns on every capture glitch ("data discontinuity in recording"); harmless noise.
    # NB soundcard re-registers an "always" filter for its warning class when imported, so capture code
    # calls suppress_capture_warnings() again right after importing it.
    from .capture._soundcard import suppress_capture_warnings

    suppress_capture_warnings()
    a = parse_args(argv)
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
