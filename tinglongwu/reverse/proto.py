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

import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .. import fmt
# #663: proto.py was split; these names stay importable from here (tests, voice.py, scripts/speak_selftest.py), and
# _live_main / _main look them up in this module, so monkeypatching proto.<name> keeps working.
from .args import (PAUSE_MAX, PAUSE_MIN, _ru_model_ok, _warn_no_ru_model, build_parser, check_args,  # noqa: F401
                   default_whisper, resolve_tts_engine)
from .listen_side import ListenSide  # noqa: F401
from .player import ABORT_JOIN_S, PLAY_BLOCK_S, Player, VramSampler, vram_used_mib  # noqa: F401
from .session import (_close_items, _drain, _record_line, _run_auto_live, _run_confirm, _stop_live,  # noqa: F401
                      _to_bar, make_segmenter, paced_file_source)
from .synth import (VOICE_ENGINES, WARM_EN, WARM_TEXT, _CYR, _JA, FollowTTS, _file_items, _make_synth,  # noqa: F401
                    _synth_sentences, _target, describe_error, no_audio_reason, warm_tts)
from .translator import (HYMT_CPU_NOTE, _mt_kind, _NoFallback, _router_warn, _UnsupportedPair,  # noqa: F401
                         build_translator, choose_speak_mt)


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
        if not stop.is_set():  # #577: the input ended on its own (file / clip), not Quit or Ctrl+C:
            _drain(player, previewer)  # play the queued audio before cleanup() aborts the players (#427)
    except KeyboardInterrupt:
        print("\nstopping...", flush=True)
    finally:
        try:
            cleanup()
        finally:
            _close_items(utterances)  # #427: ends capture_segments, which joins the capture thread (recorder released)


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
