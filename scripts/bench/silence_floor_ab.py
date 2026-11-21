#!/usr/bin/env python3
"""#1608 A/B: SILENCE_RMS 1e-3 (-60 dBFS) vs 1e-4 (-80 dBFS) on quiet speech, room noise / fan hum and music.

  python scripts/bench/silence_floor_ab.py --data DATA_DIR [--model small] [--n 30] [--device auto] [--out table.md]
(a) quiet speech: FLEURS ru clips (DATA_DIR/fleurs_ru, as fleurs_asr.py) rescaled to -60..-66 dBFS RMS -> lines shown, words recovered.
(b) room noise / fan hum at -50..-75 dBFS and (c) instrumental music at -40..-70 dBFS: deterministic synthetic signals
(seeded, no download) -> false lines per minute. A clip reaches ASR only when its RMS >= the floor (as stream.py / pipeline.py do);
lines that is_hallucination() drops are not counted. Runs in the tlwu venv; the table is also written to $GITHUB_STEP_SUMMARY."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tinglongwu.hallucination import is_hallucination  # noqa: E402

SR = 16000
FLOORS = (1e-3, 1e-4)
CLIP_S = 10.0
SPEECH_DB = (-60.0, -63.0, -66.0)
NOISE_DB = (-50.0, -60.0, -75.0)
MUSIC_DB = (-40.0, -55.0, -70.0)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def scale_to_dbfs(x: np.ndarray, dbfs: float) -> np.ndarray:
    r = rms(x)
    return (x * (10 ** (dbfs / 20) / r)).astype(np.float32) if r > 0 else x.astype(np.float32)


def synth(kind: str, dbfs: float, seconds: float = CLIP_S, seed: int = 0) -> np.ndarray:
    """Deterministic non-speech: 'noise' (pink-ish room), 'hum' (120 Hz fan + harmonics + hiss), 'music' (chord with note envelope)."""
    rng = np.random.default_rng(seed)
    n, t = int(seconds * SR), np.arange(int(seconds * SR)) / SR
    if kind == "noise":
        spec = np.fft.rfft(rng.standard_normal(n))
        spec /= np.sqrt(np.maximum(np.arange(len(spec)), 1))
        x = np.fft.irfft(spec, n)
    elif kind == "hum":
        x = sum(np.sin(2 * np.pi * 120 * k * t) / k for k in (1, 2, 3)) + 0.05 * rng.standard_normal(n)
    elif kind == "music":
        x = np.zeros(n)
        for i, f in enumerate((261.6, 329.6, 392.0, 523.3)):
            x += np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.sin(2 * np.pi * (0.5 + 0.1 * i) * t)) ** 2
    else:
        raise ValueError(kind)
    return scale_to_dbfs(x, dbfs)


def reaches_asr(clip: np.ndarray, floor: float) -> bool:
    return rms(clip) >= floor


def count_false_lines(clips, floor, transcribe) -> int:
    """Lines shown for non-speech clips: clip passes the floor, ASR returns text, hallucination filter keeps it."""
    n = 0
    for c in clips:
        if not reaches_asr(c, floor):
            continue
        t = transcribe(c)
        if t is not None and t.text.strip() and not is_hallucination(t.text, rms(c), t.no_speech):
            n += 1
    return n


def speech_stats(clips, floor, transcribe):
    """(lines shown, words recovered) over quiet-speech clips."""
    lines = words = 0
    for c in clips:
        if not reaches_asr(c, floor):
            continue
        t = transcribe(c)
        if t is not None and t.text.strip() and not is_hallucination(t.text, rms(c), t.no_speech):
            lines += 1
            words += len(t.text.split())
    return lines, words


def table(rows) -> str:
    out = ["| set | floor (dBFS) | lines shown | words emitted | false lines/min |", "|---|---|---|---|---|"]
    for name, floor, lines, words, fpm in rows:
        out.append(f"| {name} | {20 * np.log10(floor):.0f} | {lines} | {words if words is not None else '-'} | "
                   f"{f'{fpm:.2f}' if fpm is not None else '-'} |")
    return "\n".join(out)


def speech_clips(root: Path, n: int):
    paths = sorted((root / "fleurs_ru" / "audio").rglob("*.wav"))[:n]
    if not paths:
        raise SystemExit(f"no FLEURS wav files under {root / 'fleurs_ru' / 'audio'}")
    import soundfile as sf
    clips = []
    for i, p in enumerate(paths):
        x, sr = sf.read(str(p), dtype="float32")
        assert sr == SR
        clips.append(scale_to_dbfs(x, SPEECH_DB[i % len(SPEECH_DB)]))
    return clips


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.device import resolve_device
    from tinglongwu.models import models_dir
    dev = resolve_device(a.device)

    def fresh():  # the recognizer keeps state between calls (prompt, language): new one per set and floor
        return FasterWhisperRecognizer(models_dir(None), a.model, "auto", dev)
    sets = {
        "(a) quiet speech": ("speech", speech_clips(Path(a.data), a.n)),
        "(b) room noise / fan hum": ("fp", [synth(k, d, seed=i) for i, (k, d) in enumerate(
            (k, d) for k in ("noise", "hum") for d in NOISE_DB for _ in range(5))]),
        "(c) music": ("fp", [synth("music", d, seed=i) for i, d in enumerate(d for d in MUSIC_DB for _ in range(10))]),
    }
    rows = []
    for name, (kind, clips) in sets.items():
        minutes = sum(len(c) for c in clips) / SR / 60
        for floor in FLOORS:
            if kind == "speech":
                l, w = speech_stats(clips, floor, fresh().transcribe)
                rows.append((name, floor, l, w, None))
            else:
                f = count_false_lines(clips, floor, fresh().transcribe)
                rows.append((name, floor, f, None, f / minutes))
    text = table(rows)
    print(text)
    for dest in (a.out, os.environ.get("GITHUB_STEP_SUMMARY", "")):
        if dest:
            with open(dest, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
