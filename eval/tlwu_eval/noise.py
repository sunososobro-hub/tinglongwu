"""Silence / ambient-noise hallucination test (#121): clips with no speech must produce no subtitle.

Four categories: silence, aircon (steady low-passed noise), keyboard (short clicks), distant (faint speech-like
babble). Clips are synthesised deterministically (seeded numpy) so every run is repeatable; real recordings dropped
into `<dir>/<category>/*.wav` (outside the repo, see --noise-dir) replace the synthetic clip.
A clip counts as a hallucination when the ASR returns text the pipeline would still show
(non-empty and not removed by `tinglongwu.hallucination.is_hallucination`).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
CATEGORIES = ("silence", "aircon", "keyboard", "distant")
CLIP_S = 10.0


def _scale(x, dbfs):
    rms = float(np.sqrt(np.mean(x ** 2))) or 1.0
    return (x * (10 ** (dbfs / 20) / rms)).astype(np.float32)


def synth(category: str, seconds: float = CLIP_S, seed: int = 0) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SAMPLE_RATE
    if category == "silence":
        return np.zeros(n, np.float32)
    if category == "aircon":  # steady broadband rumble: low-passed noise plus a 100 Hz hum
        noise = np.convolve(rng.standard_normal(n), np.ones(16) / 16, mode="same")
        return _scale(noise + 0.5 * np.sin(2 * np.pi * 100 * t), -38)
    if category == "keyboard":  # 15 ms decaying clicks at random times, quiet background
        x = rng.standard_normal(n) * 0.002
        click = rng.standard_normal(240) * np.exp(-np.arange(240) / 40)
        for at in rng.integers(0, n - 240, int(seconds * 4)):
            x[at:at + 240] += click * 0.1
        return x.astype(np.float32)
    if category == "distant":  # syllable-rate modulated harmonic stack, very quiet
        f0 = 120 + 20 * np.sin(2 * np.pi * 0.7 * t)
        carrier = sum(np.sin(2 * np.pi * k * np.cumsum(f0) / SAMPLE_RATE) / k for k in range(1, 12))
        env = np.clip(np.sin(2 * np.pi * 3.5 * t), 0, None) * (np.sin(2 * np.pi * 0.3 * t) > -0.2)
        return _scale(carrier * env + 0.05 * rng.standard_normal(n), -48)
    raise ValueError(f"unknown noise category {category!r}")


def build_clips(real_dir=None) -> list[dict]:
    """[{category, id, audio}] : one synthetic clip per category, plus every real .wav under real_dir/<category>/."""
    clips = [{"category": c, "id": f"{c}_synth", "audio": synth(c, seed=i)} for i, c in enumerate(CATEGORIES)]
    if real_dir:
        from tinglongwu.capture.file import FileSource

        for c in CATEGORIES:
            for wav in sorted((Path(real_dir) / c).glob("*.wav")):
                clips.append({"category": c, "id": f"{c}_{wav.stem}", "audio": FileSource(str(wav))._load()})
    return clips


def hallucination_count(text: str, audio) -> bool:
    from tinglongwu.hallucination import is_hallucination

    text = (text or "").strip()
    if not text:
        return False
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    return not is_hallucination(text, rms=rms)


def run_noise(asr, clips) -> dict:
    """asr: object with .transcribe(float32 16 kHz) -> Transcript-like or None. Report per category:
    count of clips with shown text, total, rate x100 (2 decimals) and the hallucinated texts; any count > 0 is red."""
    cats = {}
    for c in clips:
        tr = asr.transcribe(c["audio"])
        text = (getattr(tr, "text", "") or "").strip()
        e = cats.setdefault(c["category"], {"count": 0, "total": 0, "texts": []})
        e["total"] += 1
        if hallucination_count(text, c["audio"]):
            e["count"] += 1
            e["texts"].append({"id": c["id"], "text": text})
    for e in cats.values():
        e["rate"] = round(100.0 * e["count"] / e["total"], 2)
    count, total = sum(e["count"] for e in cats.values()), sum(e["total"] for e in cats.values())
    return {"categories": cats, "count": count, "total": total,
            "rate": round(100.0 * count / total, 2) if total else 0.0, "stage": "FAIL" if count else "PASS"}
