"""Reverse-mode prototype: Chinese wav(s) -> Whisper (zh) -> Qwen3 zh->ru -> Qwen3-TTS (subprocess) -> speakers + wav.

Run (main .venv):
  python -m tinglongwu.reverse.proto --input C:\\...\\reverse\\input --out C:\\...\\reverse\\out \\
      --tts-python C:\\...\\tts-venv\\Scripts\\python.exe --tts-model C:\\...\\models\\qwen3-tts-0.6b-base \\
      --ref ref_ru.wav --ref-text "..." [--qwen qwen3-8b] [--device "CABLE Input"] [--no-play]
All results (UTF-8) go to --report (JSON).
Default = confirm-before-send: each utterance shows zh / ru / back-translation; Enter sends the Russian audio to
--device, "r" or "x" + Enter discards it (--no-confirm = old automatic behaviour; --preview-device = also hear it).
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np


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
        self.speaker_name = None
        if enabled:
            import soundcard as sc

            from .logic import match_device

            speakers = sc.all_speakers()
            idx = match_device([s.name for s in speakers], device)
            spk = speakers[idx] if idx is not None else sc.default_speaker()
            self.speaker_name = spk.name
            self.t = threading.Thread(target=self._run, args=(spk,), daemon=True)
            self.t.start()

    def _run(self, spk):
        with spk.player(samplerate=self.out_sr, channels=1, blocksize=1024) as p:
            while True:
                item = self.q.get()
                if item is None:
                    return
                p.play(item.reshape(-1, 1))

    def push(self, audio: np.ndarray, sr: int) -> None:
        if self.enabled:
            from .logic import resample

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


def _make_synth(tts):
    from .logic import split_sentences

    def synth(ru: str):
        pcm, sr_out = [], 24000
        for sent in split_sentences(ru):
            for ev, data in tts.speak(sent):
                if ev["t"] == "chunk":
                    pcm.append(np.frombuffer(data, dtype=np.float32))
                    sr_out = ev["sr"]
        return (np.concatenate(pcm), sr_out) if pcm else None

    return synth


def _target(a) -> str:
    return getattr(a, "to", "ru")


def _run_confirm(a, items, mt, tts, player, previewer, res, sess_box=None) -> None:
    """ASR+MT+back-translation in a producer thread; this thread asks for confirmation; TTS runs in the background.
    `items` yields (n, label, zh)."""
    from .confirm import ConfirmSession, Utterance, back_translate
    from .logic import translate_ru

    lang = _target(a)

    def set_voice(n: int) -> None:
        tts.speaker = n

    sess = ConfirmSession(_make_synth(tts), player.push, previewer.push if previewer.enabled else None,
                          out=lambda m: print(m, flush=True),
                          set_voice=set_voice if getattr(tts, "engine", "") in VOICE_ENGINES else None)
    if sess_box is not None:
        sess_box.append(sess)

    def produce():
        try:
            for n, label, zh in items:
                ru, retries = translate_ru(lambda x: mt.translate(x, "zh", lang).text, zh, lang)
                u = Utterance(n, zh, ru, back_translate(mt, ru, lang), extra={"file": label, "cjk_retries": retries},
                              lang=lang)
                res["items"].append({"file": label, "zh": zh, lang: ru, "back": u.back, "_u": u})
                sess.submit(u)
        except Exception as e:  # noqa: BLE001
            print(f"input stopped: {e}", flush=True)
        finally:
            sess.finish()

    th = threading.Thread(target=produce, daemon=True)
    th.start()
    sess.run()
    th.join(5)
    for it in res["items"]:
        u = it.pop("_u", None)
        it["decision"] = u.decision if u else ""


def _run_auto_live(items, mt, tts, player, res, lang: str = "ru") -> None:
    """--no-confirm with live input: translate + speak every utterance as it arrives."""
    from .logic import translate_ru

    synth = _make_synth(tts)
    for n, label, zh in items:
        ru, _ = translate_ru(lambda x: mt.translate(x, "zh", lang).text, zh, lang)
        print(f"\n--- #{n} ---\nZH : {zh}\n{lang.upper()} : {ru}", flush=True)
        out = synth(ru)
        if out:
            player.push(*out)
        res["items"].append({"file": label, "zh": zh, lang: ru, "decision": "sent" if out else "no_audio"})


def _live_main(a, asr, mt, tts, player, previewer, confirm, res) -> None:
    """Mic -> VAD segments -> Whisper(zh) -> same pipeline. Ctrl+C stops capture and kills the TTS subprocess."""
    from ..capture.mic import MicSource
    from ..segment import Segmenter
    from .live import capture_segments, recognize_utterances

    stop = threading.Event()
    items = ((n, "mic", zh) for n, zh in enumerate(
        recognize_utterances(capture_segments(MicSource(a.mic_name), Segmenter(a.chunk_s, True), stop), asr), 1))
    box: list = []
    print("listening... speak Chinese; Ctrl+C to quit", flush=True)
    try:
        if tts is None:  # text only: show zh -> ru, nothing is spoken
            from .logic import translate_ru

            lang = _target(a)
            for _n, _src, zh in items:
                ru, _r = translate_ru(lambda x: mt.translate(x, "zh", lang).text, zh, lang)
                print(f"[ZH] {zh}\n[{lang.upper()}] {ru}\n", flush=True)
        elif confirm:
            _run_confirm(a, items, mt, tts, player, previewer, res, box)
        else:
            _run_auto_live(items, mt, tts, player, res, _target(a))
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


def build_translator(kind: str, device: str, mdir, qwen: str, to: str = "ru"):
    """zh->ru|en translator. qwen (GPU, best; any target) or opus (Argos/OPUS on CPU). ru = pivot zh->en->ru, needs
    `tlwu-setup --pair zh-en --pair en-ru --pair ru-en --pair en-zh`; en = direct zh->en, needs
    `tlwu-setup --pair zh-en --pair en-zh` (en-zh only for the back-translation shown while confirming).
    auto = qwen on CUDA, opus otherwise."""
    if kind == "auto":
        kind = "qwen" if device == "cuda" else "opus"
    if kind == "qwen":
        from ..qwen import QwenTranslator

        mt = QwenTranslator(mdir, qwen, "tw", device)
        mt.preload()
        return mt
    from ..translate import PivotTranslator

    mt = PivotTranslator(mdir, "tw", device="cpu" if device != "cuda" else "cuda")
    mt.preload("zh", to)  # fail early if the models are missing
    mt.preload(to, "zh")  # back-translation for the confirm step
    mt.history, mt._pair = [], None  # same surface as QwenTranslator for confirm.back_translate
    return mt


def check_args(a) -> str | None:
    """Validate/normalise cross-option rules in place; returns an error message or None."""
    if a.speaker is None:
        a.speaker = 0 if a.tts_engine == "kokoro" else 4
    if a.tts_engine == "kokoro" and a.to != "en":
        return "--tts-engine kokoro speaks English only: use --to en"
    if a.tts_engine == "vosk" and a.to != "ru":
        return "--tts-engine vosk speaks Russian only: use --to ru (or --tts-engine kokoro for --to en)"
    if a.to == "en" and a.tts_engine == "qwen" and not a.no_tts:
        return "--to en needs --tts-engine kokoro (the qwen TTS path is Russian-only)"
    return None


def main(argv=None) -> int:
    vm: list = []
    try:
        return _main(argv, vm)
    finally:
        for v in vm:
            v.close()


def _main(argv, vm) -> int:
    ap = argparse.ArgumentParser(prog="tinglongwu.reverse.proto")
    ap.add_argument("--input", default=None, help="dir of zh wavs (16 kHz mono) or one wav (not needed with --mic)")
    ap.add_argument("--mic", nargs="?", const="", default=None, metavar="DEVICE",
                    help="live microphone input (Chinese); optional device-name substring, default = system mic")
    ap.add_argument("--list-mics", action="store_true", help="print input device names and exit")
    ap.add_argument("--out", default=None, help="output dir (default: reverse_out)")
    ap.add_argument("--report", default=None, help="JSON result path (default <out>/result.json)")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--whisper", default=None, help="default: large-v3-turbo on CUDA, small on CPU")
    ap.add_argument("--compute", default="auto", choices=["auto", "cpu", "cuda"],
                    help="where ASR/MT/TTS run; auto = CUDA if usable, else CPU")
    ap.add_argument("--to", default="ru", choices=["ru", "en"],
                    help="spoken language: ru (default; OPUS = pivot zh->en->ru) or en (OPUS = direct zh->en, "
                         "no pivot; use with --tts-engine kokoro)")
    ap.add_argument("--mt", default="auto", choices=["auto", "qwen", "opus"],
                    help="translator; auto = Qwen3 on CUDA, OPUS/Argos on CPU (ru: pivot zh->en->ru; en: direct zh->en)")
    ap.add_argument("--no-vmic", action="store_true",
                    help="Linux: do not create the tlwu_sink/tlwu_mic virtual microphone (use --device yourself)")
    ap.add_argument("--list-speakers", action="store_true", help="print output device names and exit")
    ap.add_argument("--qwen", default="qwen3-4b", choices=["qwen3-4b", "qwen3-8b"])
    ap.add_argument("--tts-engine", default="qwen", choices=["qwen", "vosk", "kokoro"],
                    help="vosk = Vosk-TTS Russian built-in voices, fast on CPU (--tts-model = vosk model dir); "
                         "kokoro = Kokoro English voices (--tts-model = dir with kokoro-v1.0.int8.onnx + "
                         "voices-v1.0.bin; --tts-python = its own venv: GPL espeak stays out of this process)")
    ap.add_argument("--speaker", type=int, default=None,
                    help="built-in voice id 0-4 (vosk ru 0.7 default 4; kokoro default 0 = am_michael, 1 af_heart, "
                         "2 am_adam, 3 af_bella, 4 bm_george); switch live with v0-v4")
    ap.add_argument("--tts-python", default="")
    ap.add_argument("--tts-model", default="")
    ap.add_argument("--ref", default="")
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--xvec-only", action="store_true")
    ap.add_argument("--chunk", type=int, default=12)
    ap.add_argument("--device", default=None, help='output device name substring, e.g. "CABLE Input"')
    ap.add_argument("--out-sr", type=int, default=48000)
    ap.add_argument("--no-confirm", action="store_true", help="send automatically (no confirmation step)")
    ap.add_argument("--preview-device", default=None,
                    help="also play the sent Russian audio on this device (your own headphones); default off")
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--no-tts", action="store_true", help="ASR + MT only (no TTS subprocess)")
    a = ap.parse_args(argv)
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
    from .vmic import SINK, VirtualMic, is_linux

    if is_linux() and not a.no_vmic and not (a.no_play or a.no_tts) and a.device is None:
        try:
            vm.append(VirtualMic().open())
        except RuntimeError as e:
            print(f"error: could not create the virtual microphone: {e}", file=sys.stderr)
            return 2
        a.device = SINK
        print(f"virtual microphone created: select 'tlwu_mic' as the microphone in Meet/Zoom "
              f"(removed when this program exits)", flush=True)
    live = a.mic is not None
    if not live and not a.input:
        ap.error("--input is required unless --mic is given")
    a.out = a.out or "reverse_out"
    a.chunk_s = 10.0
    if live:  # feedback guard BEFORE loading any model
        import soundcard as sc

        from .live import check_mic_feedback, resolve_mic_name

        dm = sc.default_microphone()
        try:
            a.mic_name = resolve_mic_name(a.mic or None, [m.name for m in sc.all_microphones(include_loopback=False)],
                                          dm.name if dm else None)
            spk = None
            try:
                from .logic import match_device

                sp = sc.all_speakers()
                k = match_device([x.name for x in sp], a.device)
                spk = sp[k].name if k is not None else None
            except LookupError:
                pass
            check_mic_feedback(a.mic_name, a.device, spk, same_name_ok=is_linux())
        except (LookupError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"microphone: {a.mic_name}", flush=True)

    if a.ref_text.startswith("@"):  # "@file.txt": read the transcript from a UTF-8 file (console-safe)
        a.ref_text = Path(a.ref_text[1:]).read_text(encoding="utf-8").strip()

    import soundfile as sf

    from .. import offline  # noqa: F401  (applies offline env + network guard)
    from ..asr import FasterWhisperRecognizer
    from ..models import models_dir
    from .logic import split_sentences, translate_ru
    from .tts_client import TTSClient

    from ..device import resolve_device

    dev = resolve_device(a.compute)
    a.whisper = a.whisper or ("large-v3-turbo" if dev == "cuda" else "small")
    print(f"compute device: {dev}", flush=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.input) if a.input else None
    wavs = [] if live else (sorted(src.glob("*.wav")) if src.is_dir() else [src])
    mdir = models_dir(a.models_dir)
    res: dict = {"qwen": a.qwen, "whisper": a.whisper, "ref": a.ref, "xvec_only": a.xvec_only, "items": [],
                 "vram_mib": {"baseline": vram_used_mib()}}
    with VramSampler() as vs:
        t = time.perf_counter()
        asr = FasterWhisperRecognizer(mdir, a.whisper, "zh", dev)
        res["load_s"] = {"whisper": round(time.perf_counter() - t, 2)}
        res["vram_mib"]["after_whisper"] = vram_used_mib()
        t = time.perf_counter()
        mt = build_translator(a.mt, dev, mdir, a.qwen, a.to)
        res["load_s"]["qwen"] = round(time.perf_counter() - t, 2)
        res["vram_mib"]["after_qwen"] = vram_used_mib()
        t = time.perf_counter()
        tts = None if a.no_tts else TTSClient(a.tts_python, a.tts_model, a.ref, a.ref_text, a.xvec_only, a.chunk,
                                        "cuda" if dev == "cuda" else "cpu", a.tts_engine, a.speaker)
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
        mt.translate("你好。", "zh", a.to)
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
            t0 = time.perf_counter()  # "end of Chinese speech": the segment is complete and handed over
            tr = asr.transcribe(audio)
            t_asr = time.perf_counter() - t0
            zh = tr.text if tr else ""
            retries = 0
            if zh:
                ru, retries = translate_ru(lambda x: mt.translate(x, "zh", a.to).text, zh, a.to)
            else:
                ru = ""
            t_mt = time.perf_counter() - t0
            item = {"file": wav.name, "audio_s": round(len(audio) / sr, 2), "zh": zh, a.to: ru,
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
                if pcm:
                    sf.write(str(out / f"{wav.stem}_{a.to}{k}.wav"), np.concatenate(pcm), sr_out)
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
    rp = Path(a.report) if a.report else out / "result.json"
    rp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"peak VRAM {vs.peak} MiB; wrote {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
