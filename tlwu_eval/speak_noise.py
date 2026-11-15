"""Speak-mode noise gate eval (#968): keyboard / mouse / fan noise must never become a sent line, real speech must.

    python -m tlwu_eval.speak_noise --data eval_data/matrix --models-dir ~/tlwu-models --out speak_noise.json

The clip set:
- noise, synthesised here (seeded numpy, no audio files in the repo): `keyboard` (30 s of typing close to a laptop
  mic: press + release transients, word gaps, space-bar thuds, quiet room tone), `mouse` (20 s of single and double
  clicks) and `fan` (5 s digital silence, then 15 s fan rumble + 100 Hz hum);
- speech: the first N (default 10) `lang` rows of a `scripts/bench/make_ci_smoke.py` manifest (FLEURS zh, CC-BY 4.0,
  pinned by sha256 in eval/matrix_pins.json).

Every clip goes through what the GUI speak path runs (gui/app.py -> cli.run_session with --voice): the listen
segmenter (`cli.listen_segmenter`, `--preset meeting`), Whisper (`asr.FasterWhisperRecognizer`, CPU) and
`reverse.gate.segment_gate` before the line can reach the voice. A line "sent" = a segment the gate let through with
text. Each noise clip runs twice: through the VAD (what the app does) and with `--no-vad` (8 s blocks straight to
Whisper), because Silero rejects most synthetic noise outright and only the second run shows what the Whisper limits
alone do with noise that a VAD lets through (the owner's real keyboard did get through). PASS = 0 lines sent from
noise in both runs and >= `min_speech` speech clips with at least one line sent.

The report also lists every segment's Whisper stats (no_speech_prob, avg_logprob, rms, seconds, gate reason), and
`thresholds` summarises how far the noise and speech segments are apart on each gate limit: the numbers behind
reverse/gate.py's NO_SPEECH_MAX / LOGPROB_MIN.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

SR = 16000
NOISE = ("keyboard", "mouse", "fan")
SECONDS = {"keyboard": 30.0, "mouse": 20.0, "fan": 20.0}


def _transient(rng, n: int, decay: float, lowpass: int) -> np.ndarray:
    """A short decaying noise burst; `lowpass` taps of moving average darken it (1 = bright click)."""
    x = rng.standard_normal(n) * np.exp(-np.arange(n) / decay)
    if lowpass > 1:
        x = np.convolve(x, np.ones(lowpass) / lowpass, mode="same")
    return x / (np.max(np.abs(x)) or 1.0)


def synth(kind: str, seconds: float | None = None, seed: int = 968) -> np.ndarray:
    """Deterministic noise clip, float32 mono 16 kHz. kind in NOISE."""
    seconds = SECONDS[kind] if seconds is None else seconds
    n = int(seconds * SR)
    rng = np.random.default_rng([seed, NOISE.index(kind)])
    t = np.arange(n) / SR
    if kind == "keyboard":
        x = rng.standard_normal(n) * 0.0015  # room tone
        at = 0.2
        while at < seconds - 0.3:
            word = int(rng.integers(2, 9))
            for _ in range(word):
                i = int(at * SR)
                peak = float(rng.uniform(0.08, 0.35))
                press = _transient(rng, int(0.012 * SR), 0.0025 * SR, int(rng.integers(1, 4)))
                x[i:i + len(press)] += press * peak
                j = i + int(rng.uniform(0.06, 0.12) * SR)  # key release, quieter
                rel = _transient(rng, int(0.008 * SR), 0.0015 * SR, 1)
                x[j:j + len(rel)] += rel * peak * 0.4
                at += float(rng.uniform(0.09, 0.22))
            i = int(at * SR)  # space bar: lower, longer thud
            thud = _transient(rng, int(0.03 * SR), 0.006 * SR, 8)
            x[i:i + len(thud)] += thud * float(rng.uniform(0.2, 0.45))
            at += float(rng.uniform(0.15, 0.6)) + (float(rng.uniform(0.8, 2.0)) if rng.random() < 0.15 else 0.0)
        return np.clip(x, -1, 1).astype(np.float32)
    if kind == "mouse":
        x = rng.standard_normal(n) * 0.001
        at = 0.5
        while at < seconds - 0.5:
            for k in range(2 if rng.random() < 0.3 else 1):  # double click 30 % of the time
                i = int((at + 0.12 * k) * SR)
                c = _transient(rng, int(0.006 * SR), 0.001 * SR, 1)
                x[i:i + len(c)] += c * float(rng.uniform(0.1, 0.4))
            at += float(rng.uniform(0.4, 2.5))
        return np.clip(x, -1, 1).astype(np.float32)
    if kind == "fan":
        x = np.zeros(n)
        start = int(5.0 * SR)
        rumble = np.convolve(rng.standard_normal(n - start), np.ones(24) / 24, mode="same")
        rumble = rumble / (np.sqrt(np.mean(rumble ** 2)) or 1.0) * 0.006
        x[start:] = rumble + 0.003 * np.sin(2 * np.pi * 100 * t[start:])
        return x.astype(np.float32)
    raise ValueError(f"unknown noise kind {kind!r}")


def speech_clips(data_dir, lang: str = "zh", count: int = 10) -> list[dict]:
    """[{kind: 'speech', id, audio, ref}] from a make_ci_smoke.py manifest (rows of `lang`, manifest order)."""
    from tinglongwu.capture.file import FileSource

    from .runner import load_manifest

    rows = [r for r in load_manifest(data_dir) if r["lang"] == lang][:count]
    return [{"kind": "speech", "id": r["id"], "audio": FileSource(r["wav"])._load(), "ref": r.get("ref_text", "")}
            for r in rows]


def noise_clips(seed: int = 968) -> list[dict]:
    return [{"kind": k, "id": f"{k}_synth", "audio": synth(k, seed=seed)} for k in NOISE]


def _array_source(audio: np.ndarray, block_s: float = 0.5):
    """AudioSource over an in-memory clip, 0.5 s frames (FileSource without the file)."""
    from tinglongwu.capture.base import AudioSource

    class ArraySource(AudioSource):
        def frames(self):
            for i in range(0, len(audio), int(block_s * SR)):
                yield audio[i:i + int(block_s * SR)]

    return ArraySource()



class _Lines:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, r) -> None:
        text = (getattr(r, "original", "") or "").strip()
        if text:
            self.lines.append(text)

    def write_source(self, *_a, **_k) -> None:
        pass


def speak_args(lang: str = "zh", extra=()):
    """The CLI args the GUI speak path passes for the segmenter (meeting preset, VAD on)."""
    from tinglongwu.cli import parse_args

    return parse_args(["--file", "clip.wav", "--lang", lang, "--transcript-only", "--preset", "meeting", *extra])


def run_clip(clip: dict, asr, a, env=None) -> dict:
    """One clip through segmenter -> ASR -> speak gate. Returns {id, kind, sent: [text], segments: [stats]}."""
    from tinglongwu import pipeline
    from tinglongwu.cli import listen_segmenter
    from tinglongwu.reverse.gate import segment_gate

    segs: list[dict] = []
    gate = segment_gate(env=env, report=_Quiet())

    def seg_gate(seg, tr) -> bool:
        ok = gate(seg, tr)
        rms = float(np.sqrt(np.mean(np.square(seg)))) if len(seg) else 0.0
        segs.append({"seconds": round(len(seg) / SR, 2), "rms": round(rms, 5),
                     "text": (getattr(tr, "text", "") or "").strip(),
                     "no_speech": round(float(getattr(tr, "no_speech", 0.0) or 0.0), 4),
                     "avg_logprob": round(float(getattr(tr, "avg_logprob", 0.0) or 0.0), 4),
                     "reason": "" if ok else _reason(seg, tr, env)})
        return ok

    sink = _Lines()
    pipeline.run(_array_source(clip["audio"]), listen_segmenter(a), asr, None, sink, a.to, None, audio_clock=True,
                 seg_gate=seg_gate)
    return {"id": clip["id"], "kind": clip["kind"], "sent": sink.lines, "segments": segs}


class _Quiet:
    def add(self, _reason) -> None:
        pass


def _reason(seg, tr, env) -> str:
    from tinglongwu.reverse.gate import SegmentStats, gate

    rms = float(np.sqrt(np.mean(np.square(seg)))) if len(seg) else 0.0
    return gate(SegmentStats(len(seg) / SR, rms, (getattr(tr, "text", "") or "").strip(),
                             float(getattr(tr, "no_speech", 0.0) or 0.0),
                             float(getattr(tr, "avg_logprob", 0.0) or 0.0)), env)[1]


def thresholds(results: list[dict]) -> dict:
    """How far apart noise and speech segments are on the two Whisper limits. Only segments Whisper returned text
    for count (a segment with no text can never be sent). `margin` > 0 means a single limit separates them."""
    noise = [s for r in results if r["kind"] != "speech" for s in r["segments"] if s["text"]]
    speech = [s for r in results if r["kind"] == "speech" for s in r["segments"] if s["text"]]
    out = {"noise_segments_with_text": len(noise), "speech_segments_with_text": len(speech)}
    if speech:
        out["speech_logprob_min"] = min(s["avg_logprob"] for s in speech)
        out["speech_no_speech_max"] = max(s["no_speech"] for s in speech)
    if noise:
        out["noise_logprob_max"] = max(s["avg_logprob"] for s in noise)
        out["noise_no_speech_min"] = min(s["no_speech"] for s in noise)
    if speech and noise:
        out["logprob_margin"] = round(out["speech_logprob_min"] - out["noise_logprob_max"], 4)
        out["no_speech_margin"] = round(out["noise_no_speech_min"] - out["speech_no_speech_max"], 4)
    return out


def report(results: list[dict], min_speech: int) -> dict:
    noise_sent = [{"id": r["id"], "text": t} for r in results if r["kind"] != "speech" for t in r["sent"]]
    speech = [r for r in results if r["kind"] == "speech"]
    speech_ok = sum(1 for r in speech if r["sent"])
    segs = [s for r in speech for s in r["segments"] if s["text"]]
    dropped = [s for s in segs if s["reason"]]
    passed = not noise_sent and speech_ok >= min_speech
    return {"noise_lines_sent": len(noise_sent), "noise_sent": noise_sent,
            "speech_clips_sent": speech_ok, "speech_clips": len(speech), "min_speech": min_speech,
            "speech_segments_dropped": len(dropped), "speech_segments": len(segs),
            "thresholds": thresholds(results), "stage": "PASS" if passed else "FAIL", "clips": results}


def summary(rep: dict) -> str:
    th = rep["thresholds"]
    lines = [f"speak noise gate (#968): {rep['stage']}",
             f"noise lines sent: {rep['noise_lines_sent']} (must be 0)",
             f"speech clips with a line sent: {rep['speech_clips_sent']}/{rep['speech_clips']} "
             f"(need >= {rep['min_speech']}); speech segments dropped: "
             f"{rep['speech_segments_dropped']}/{rep['speech_segments']}",
             "thresholds: " + ", ".join(f"{k}={v}" for k, v in th.items())]
    for r in rep["clips"]:
        for s in r["segments"]:
            lines.append(f"  {r['id']:<14} {s['seconds']:>5}s rms={s['rms']:<8} no_speech={s['no_speech']:<6} "
                         f"logprob={s['avg_logprob']:<7} {('DROP ' + s['reason']) if s['reason'] else 'SENT':<18} "
                         f"{s['text'][:60]}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m tlwu_eval.speak_noise", description=__doc__.split("\n")[0])
    p.add_argument("--data", required=True, help="make_ci_smoke.py output folder (manifest.jsonl with zh rows)")
    p.add_argument("--lang", default="zh")
    p.add_argument("--speech", type=int, default=10, help="speech clips to use (default 10)")
    p.add_argument("--min-speech", type=int, default=9, help="speech clips that must send a line (default 9)")
    p.add_argument("--model", default="small")
    p.add_argument("--device", default="cpu")
    p.add_argument("--models-dir", default=None)
    p.add_argument("--out", default=None, help="write the JSON report here")
    a = p.parse_args(argv)
    from tinglongwu import offline
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.models import models_dir

    offline.set_mode("listen")  # the eval never needs the network
    mdir = models_dir(a.models_dir)
    asr = FasterWhisperRecognizer(mdir, a.model, a.lang, a.device)
    sa = speak_args(a.lang)
    novad = speak_args(a.lang, ["--no-vad"])
    results = [run_clip(c, asr, sa) for c in noise_clips()]
    results += [dict(run_clip(c, asr, novad), id=c["id"] + "_novad") for c in noise_clips()]
    results += [run_clip(c, asr, sa) for c in speech_clips(a.data, a.lang, a.speech)]
    rep = report(results, a.min_speech)
    print(summary(rep))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if rep["stage"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
