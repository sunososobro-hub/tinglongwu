#!/usr/bin/env python
"""#849: prove audio routing through VB-CABLE with a signal check (cloud Windows runner, no real sound card).

Lists every playback and capture device, records `--record` (default "CABLE Output") while a known tone is played
into `--play` (default "CABLE Input"), and passes only when
  - before the tone, the recording is quiet (RMS <= SILENCE_RMS_THRESHOLD: the check is not fooled by noise), and
  - from the tone's start to the end, the recording's RMS is above SILENCE_RMS_THRESHOLD.
Exit 0 = PASS, 1 = FAIL (a device is missing, nothing captured, or the levels are wrong). `--json F` writes the result.

    python scripts/cable_loopback_check.py [--play "CABLE Input"] [--record "CABLE Output"] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CABLE_IN, CABLE_OUT = "CABLE Input", "CABLE Output"
SILENCE_RMS_THRESHOLD = 0.01   # linear RMS (about -40 dBFS): above it the cable carried our signal
TONE_HZ = 440.0
TONE_AMPLITUDE = 0.5           # RMS of the tone itself: 0.5 / sqrt(2) = 0.35
TONE_S = 2.0
BASELINE_S = 0.6               # recorded before the tone starts
SETTLE_S = 0.25                # skipped at the start of each window (device start-up / resampler latency)
SAMPLE_RATE = 48000
START_TIMEOUT_S = 10.0         # the recorder must deliver the baseline within this


def tone(seconds: float = TONE_S, sr: int = SAMPLE_RATE, hz: float = TONE_HZ, amp: float = TONE_AMPLITUDE):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def rms(x) -> float:
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def find_device(names: list[str], wanted: str) -> str | None:
    """The first device whose name contains `wanted` (case-insensitive), like soundcard and speak_selftest do."""
    w = wanted.casefold()
    return next((n for n in names if w in n.casefold()), None)


def verdict(baseline_rms: float, tone_rms: float, threshold: float = SILENCE_RMS_THRESHOLD) -> tuple[bool, str]:
    if baseline_rms > threshold:
        return False, f"FAIL noise before the tone: RMS {baseline_rms:.4f} > {threshold}"
    if tone_rms <= threshold:
        return False, f"FAIL silence: RMS {tone_rms:.4f} <= {threshold} while the tone played"
    return True, f"PASS RMS {tone_rms:.4f} > {threshold} (baseline {baseline_rms:.4f})"


def window(rec: np.ndarray, start_s: float, end_s: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    return rec[int((start_s + SETTLE_S) * sr): int(end_s * sr)]


def _com():
    """Windows: COM on the recorder thread (soundcard only initializes it on the importing thread)."""
    if sys.platform != "win32":
        import contextlib  # noqa: PLC0415
        return contextlib.nullcontext()
    from tinglongwu.capture._soundcard import com_initialized  # noqa: PLC0415
    return com_initialized()


def check(play: str, record: str, sc=None) -> dict:
    """Play the tone into `play` while recording `record` (real devices through soundcard)."""
    speakers = [s.name for s in sc.all_speakers()]
    mics = [m.name for m in sc.all_microphones(include_loopback=False)]
    out = {"speakers": speakers, "microphones": mics, "play": play, "record": record,
           "threshold": SILENCE_RMS_THRESHOLD}
    spk_name, mic_name = find_device(speakers, play), find_device(mics, record)
    if spk_name is None or mic_name is None:
        missing = [f"playback {play!r}" for _ in [0] if spk_name is None] + \
                  [f"capture {record!r}" for _ in [0] if mic_name is None]
        out.update(ok=False, reason="FAIL device not found: " + ", ".join(missing))
        return out
    spk, mic = sc.get_speaker(spk_name), sc.get_microphone(mic_name)
    chunks, err, stop = [], [], threading.Event()
    block = SAMPLE_RATE // 20

    def rec_loop():
        try:
            with _com(), mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=block) as r:
                while not stop.is_set():
                    chunks.append(r.record(numframes=block).reshape(-1))
        except BaseException as e:  # noqa: BLE001
            err.append(f"{type(e).__name__}: {e}")

    th = threading.Thread(target=rec_loop, daemon=True)
    th.start()
    # the baseline is counted in recorded samples, not wall time: opening the recorder takes a while
    base_n, t0 = int(BASELINE_S * SAMPLE_RATE), time.monotonic()
    while sum(c.size for c in chunks) < base_n and not err and time.monotonic() - t0 < START_TIMEOUT_S:
        time.sleep(0.02)
    with spk.player(samplerate=SAMPLE_RATE, channels=1) as p:
        p.play(tone())
    time.sleep(0.3)
    stop.set()
    th.join(5)
    recd = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    if err or recd.size == 0:
        out.update(ok=False, reason=f"FAIL nothing captured from {mic_name!r}: {err[0] if err else 'no frames'}")
        return out
    base, tone_rms = rms(window(recd, 0, BASELINE_S)), rms(window(recd, BASELINE_S, recd.size / SAMPLE_RATE))
    ok, why = verdict(base, tone_rms)
    out.update(ok=ok, reason=why, baseline_rms=round(base, 5), tone_rms=round(tone_rms, 5),
               recorded_s=round(recd.size / SAMPLE_RATE, 2))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--play", default=CABLE_IN)
    ap.add_argument("--record", default=CABLE_OUT)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    import soundcard as sc  # noqa: PLC0415  (imported on this thread first: it initializes COM here)
    if sys.platform == "win32":
        from tinglongwu.capture._soundcard import patch_mixformat  # noqa: PLC0415

        patch_mixformat()
    out = check(a.play, a.record, sc)
    print("playback devices: " + "; ".join(out["speakers"]))
    print("capture devices: " + "; ".join(out["microphones"]))
    print(out["reason"])
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2), "utf-8")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
