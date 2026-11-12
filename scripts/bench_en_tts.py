#!/usr/bin/env python3
"""Offline English TTS benchmark (kokoro-onnx / supertonic). Run inside the engine's own venv.

  python bench_en_tts.py --engine kokoro --model-dir ~/models/kokoro --voice af_heart
  python bench_en_tts.py --engine supertonic --model-dir ~/models/supertonic-3 --voice M1

Local files only: never downloads. Writes ./bench_out/<engine>_<voice>_N.wav
"""
import os

# Must be set before any third-party import.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

SENTENCES = [
    "Let's meet next Tuesday at three p.m. to discuss the budget.",
    "Could you send me the report?",
    "I think we should postpone the launch until the security review is finished.",
    "Thank you, everyone, for joining the call today.",
    "Our quarterly revenue grew by twelve percent, but the marketing team still needs "
    "additional headcount before the end of the year.",
]


def write_wav(path, samples, sr):
    x = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (x * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())


def load_kokoro(model_dir, model_file):
    model = model_dir / model_file
    voices = model_dir / "voices-v1.0.bin"
    for p in (model, voices):
        if not p.is_file():
            sys.exit(f"missing local file: {p}")
    from kokoro_onnx import Kokoro  # local paths only; np.load without allow_pickle

    k = Kokoro(str(model), str(voices))

    def synth(text, voice):
        samples, sr = k.create(text, voice=voice, speed=1.0, lang="en-us")
        return samples, sr

    return synth


def load_supertonic(model_dir):
    if not model_dir.is_dir():
        sys.exit(f"missing local dir: {model_dir}")
    from supertonic import TTS

    # auto_download=False: raises instead of touching the network
    tts = TTS(model="supertonic-3", model_dir=str(model_dir), auto_download=False)
    styles = {}

    def synth(text, voice):
        if voice not in styles:
            styles[voice] = tts.get_voice_style(voice)
        wav, _dur = tts.synthesize(text, voice_style=styles[voice], lang="en")
        return np.asarray(wav, dtype=np.float32).reshape(-1), tts.sample_rate

    return synth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=["kokoro", "supertonic"])
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--voice", default=None, help="kokoro: af_heart etc; supertonic: M1..M5/F1..F5")
    ap.add_argument("--kokoro-file", default="kokoro-v1.0.int8.onnx",
                    help="kokoro model filename inside --model-dir")
    ap.add_argument("--out-dir", default="bench_out")
    a = ap.parse_args()

    model_dir = Path(a.model_dir).expanduser()
    voice = a.voice or ("af_heart" if a.engine == "kokoro" else "M1")
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    synth = load_kokoro(model_dir, a.kokoro_file) if a.engine == "kokoro" else load_supertonic(model_dir)
    load_s = time.perf_counter() - t0
    print(f"engine={a.engine} voice={voice} load_time={load_s:.2f}s")

    t0 = time.perf_counter()
    synth("Warm up.", voice)  # first call pays one-time ORT init; reported separately
    print(f"warmup_synth={time.perf_counter() - t0:.2f}s (excluded below)")

    print(f"{'#':>2} {'audio_s':>8} {'synth_s':>8} {'RTF':>6}  words")
    tot_a = tot_s = 0.0
    for i, text in enumerate(SENTENCES, 1):
        t0 = time.perf_counter()
        samples, sr = synth(text, voice)
        dt = time.perf_counter() - t0
        dur = len(samples) / sr
        tot_a += dur
        tot_s += dt
        write_wav(out / f"{a.engine}_{voice}_{i}.wav", samples, sr)
        print(f"{i:>2} {dur:>8.2f} {dt:>8.2f} {dt / dur:>6.3f}  {len(text.split())}")
    print(f"total audio={tot_a:.2f}s synth={tot_s:.2f}s overall_RTF={tot_s / tot_a:.3f} (RTF<1 = faster than real time)")


if __name__ == "__main__":
    main()
