#!/usr/bin/env python
"""#207: time-to-first-audio and gaps of streamed TTS, through the real worker, without an audio device.

    python scripts/bench_tts_stream.py --tts-python ~/tts-kokoro/bin/python --tts-model ~/models/kokoro

For each test sentence it times (a) whole sentences per request (before #207) and (b) speech_pieces (after), and
prints first-audio p50/max and the longest gap the player would have had (GapMeter: each piece starts when the
previous one ends). Nothing is played or written.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tinglongwu.reverse.logic import GapMeter, speech_pieces, split_sentences  # noqa: E402
from tinglongwu.reverse.tts_client import TTSClient  # noqa: E402

TEXTS = [
    "I think we should start the meeting now, because everyone is already here.",
    "Today we will look at the quarterly numbers and then talk about hiring for the new office in Taipei, "
    "which opens next spring.",
    "Thank you.",
    "The project is about two weeks behind schedule, mainly because the supplier changed the parts, "
    "so we need to decide today whether to delay the launch or reduce the scope of the first release.",
    "Could you send me the report after the meeting?",
    "We tested the new version on three different computers and it worked well on all of them, "
    "but the older laptop was a little slower than we expected.",
]


def run(tts, text: str, split) -> tuple[float, float, float]:
    t0 = time.perf_counter()
    first, gaps = None, GapMeter()
    for piece in split(text):
        for ev, data in tts.speak(piece):
            if ev["t"] == "chunk":
                first = first if first is not None else time.perf_counter() - t0
                gaps.push(len(data) / 4 / ev["sr"])
    return first or 0.0, gaps.max, time.perf_counter() - t0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts-python", required=True)
    ap.add_argument("--tts-model", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args(argv)
    tts = TTSClient(a.tts_python, a.tts_model, "kokoro", 0, "en")
    try:
        list(tts.speak("Warm up."))
        for name, split in (("sentences (before)", split_sentences), ("speech_pieces (#207)", speech_pieces)):
            ttfa, gap = [], []
            for _ in range(a.rounds):
                for t in TEXTS:
                    f, g, _total = run(tts, t, split)
                    ttfa.append(f)
                    gap.append(g)
            print(f"{name:22s} first_audio p50={statistics.median(ttfa):.2f}s max={max(ttfa):.2f}s "
                  f"gap_max={max(gap):.2f}s n={len(ttfa)}")
    finally:
        tts.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
