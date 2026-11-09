"""Reverse-mode prototype: Chinese wav(s) -> Whisper (zh) -> Qwen3 zh->ru -> Qwen3-TTS (subprocess) -> speakers + wav.

Run (main .venv):
  python -m tinglongwu.reverse.proto --input C:\\...\\reverse\\input --out C:\\...\\reverse\\out \\
      --tts-python C:\\...\\tts-venv\\Scripts\\python.exe --tts-model C:\\...\\models\\qwen3-tts-0.6b-base \\
      --ref ref_ru.wav --ref-text "..." [--qwen qwen3-8b] [--device "CABLE Input"] [--no-play]
All results (UTF-8) go to --report (JSON); nothing Cyrillic/CJK is printed to the console.
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tinglongwu.reverse.proto")
    ap.add_argument("--input", required=True, help="dir of zh wavs (16 kHz mono) or one wav")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None, help="JSON result path (default <out>/result.json)")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--whisper", default="large-v3-turbo")
    ap.add_argument("--qwen", default="qwen3-4b", choices=["qwen3-4b", "qwen3-8b"])
    ap.add_argument("--tts-python", default="")
    ap.add_argument("--tts-model", default="")
    ap.add_argument("--ref", default="")
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--xvec-only", action="store_true")
    ap.add_argument("--chunk", type=int, default=12)
    ap.add_argument("--device", default=None, help='output device name substring, e.g. "CABLE Input"')
    ap.add_argument("--out-sr", type=int, default=48000)
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--no-tts", action="store_true", help="ASR + MT only (no TTS subprocess)")
    a = ap.parse_args(argv)

    if a.ref_text.startswith("@"):  # "@file.txt": read the transcript from a UTF-8 file (console-safe)
        a.ref_text = Path(a.ref_text[1:]).read_text(encoding="utf-8").strip()

    import soundfile as sf

    from .. import offline  # noqa: F401  (applies offline env + network guard)
    from ..asr import FasterWhisperRecognizer
    from ..models import models_dir
    from ..qwen import QwenTranslator
    from .logic import split_sentences, translate_ru
    from .tts_client import TTSClient

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.input)
    wavs = sorted(src.glob("*.wav")) if src.is_dir() else [src]
    mdir = models_dir(a.models_dir)
    res: dict = {"qwen": a.qwen, "whisper": a.whisper, "ref": a.ref, "xvec_only": a.xvec_only, "items": [],
                 "vram_mib": {"baseline": vram_used_mib()}}
    with VramSampler() as vs:
        t = time.perf_counter()
        asr = FasterWhisperRecognizer(mdir, a.whisper, "zh", "cuda")
        res["load_s"] = {"whisper": round(time.perf_counter() - t, 2)}
        res["vram_mib"]["after_whisper"] = vram_used_mib()
        t = time.perf_counter()
        mt = QwenTranslator(mdir, a.qwen, "tw", "cuda")
        mt.preload()
        res["load_s"]["qwen"] = round(time.perf_counter() - t, 2)
        res["vram_mib"]["after_qwen"] = vram_used_mib()
        t = time.perf_counter()
        tts = None if a.no_tts else TTSClient(a.tts_python, a.tts_model, a.ref, a.ref_text, a.xvec_only, a.chunk)
        if tts:
            res["load_s"]["tts"] = round(time.perf_counter() - t, 2)
            res["vram_mib"]["after_tts"] = vram_used_mib()
            res["tts_torch_peak_mib_after_warmup"] = tts.ready.get("vram_mib")
        player = Player(a.device, a.out_sr, enabled=not (a.no_play or a.no_tts))
        res["speaker"] = player.speaker_name
        # warm up ASR + MT so the first sentence is not penalised by lazy init
        w0, _ = sf.read(str(wavs[0]), dtype="float32")
        asr.transcribe(w0)
        mt.translate("你好。", "zh", "ru")
        mt.history.clear()
        asr._last = ""
        for n, wav in enumerate(wavs, 1):
            audio, sr = sf.read(str(wav), dtype="float32")
            assert sr == 16000, f"{wav}: expected 16 kHz"
            t0 = time.perf_counter()  # "end of Chinese speech": the segment is complete and handed over
            tr = asr.transcribe(audio)
            t_asr = time.perf_counter() - t0
            zh = tr.text if tr else ""
            retries = 0
            if zh:
                ru, retries = translate_ru(lambda x: mt.translate(x, "zh", "ru").text, zh)
            else:
                ru = ""
            t_mt = time.perf_counter() - t0
            item = {"file": wav.name, "audio_s": round(len(audio) / sr, 2), "zh": zh, "ru": ru,
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
                    sf.write(str(out / f"{wav.stem}_ru{k}.wav"), np.concatenate(pcm), sr_out)
                item["tts"].append({"text": sent, **(done or {})})
            item["t_first_audio"] = round(first_audio, 3) if first_audio else None
            item["t_tts_done"] = round(tts_done, 3)
            res["items"].append(item)
            print(f"[{n}/{len(wavs)}] {wav.name} first_audio={item['t_first_audio']}s done={item['t_tts_done']}s",
                  flush=True)
        player.close()
        if tts:
            tts.close()
    res["vram_mib"]["peak_sampled"] = vs.peak
    rp = Path(a.report) if a.report else out / "result.json"
    rp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"peak VRAM {vs.peak} MiB; wrote {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
