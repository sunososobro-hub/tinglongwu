#!/usr/bin/env python3
"""Annotated multi-speaker "conversation" for the speaker-label eval (#1256).

The owner's field test (#1256) gave one person a new label after a short pause. This builds a public stand-in: the
first --speakers readers of LibriSpeech test-clean (CC-BY 4.0, openslr.org/12; the speaker id is in every file
path) are streamed (only the start of the archive is read), and their utterances are cut into ASR-sized pieces of
1-8 s (up to 8.6 s when a short tail joins; an utterance under 1 s stays one piece, which the eval skips like tlwu does; a third of them 1-2 s, like the short replies after a pause) and laid out as turns of a meeting: one speaker
talks for 1-3 pieces, then another one. Output: OUT/cNNNN.wav (16 kHz mono) and OUT/labels.csv ("file,speaker" in
conversation order), the input of scripts/eval_diarization.py. Fixed --seed: the same archive gives the same clip.

    python scripts/bench/diarization_clip.py --out clip --speakers 8
    python scripts/eval_diarization.py clip clip/labels.csv --model-dir M --max-label-ratio 1.5

Runs in CI (.github/workflows/eval-diarization.yml); nothing here is used by tlwu itself and no audio is stored in
the repo.
"""
from __future__ import annotations

import argparse
import csv
import http.client
import io
import random
import sys
import tarfile
import urllib.request
from pathlib import Path

SR = 16000
URLS = (  # openslr mirrors of the same file, tried in order
    "https://www.openslr.org/resources/12/test-clean.tar.gz",
    "https://us.openslr.org/resources/12/test-clean.tar.gz",
    "https://openslr.elda.org/resources/12/test-clean.tar.gz",
)
MAX_DOWNLOAD = 200 << 20  # test-clean is 346 MB for 40 speakers; 8 speakers sit in the first ~70-90 MB
UTTS_PER_SPEAKER = 12
MIN_PIECE = 0.6  # a tail shorter than this is joined to the piece before it
# piece length bands (seconds) and their weights: 1-2 s are the short noisy sentences of #1256
BANDS = ((1.0, 2.0, 0.35), (2.0, 3.0, 0.2), (3.0, 8.0, 0.45))


class DownloadError(RuntimeError):
    pass


class _Counted(io.RawIOBase):
    def __init__(self, raw, budget: dict):
        self.raw, self.budget = raw, budget

    def readable(self):
        return True

    def readinto(self, b):
        left = self.budget["max"] - self.budget["used"]
        if left <= 0:
            raise DownloadError(f"download passed {self.budget['max'] >> 20} MB")
        data = self.raw.read(min(len(b), left))  # never read past the cap
        n = len(data)
        b[:n] = data
        self.budget["used"] += n
        return n


def http_open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "tlwu-bench/0.1"})
    return urllib.request.urlopen(req, timeout=120)


def speaker_of(name: str) -> str | None:
    """'LibriSpeech/test-clean/1089/134686/1089-134686-0000.flac' -> '1089'; None for other members."""
    base = name.rsplit("/", 1)[-1]
    if not base.endswith(".flac"):
        return None
    parts = base[:-5].split("-")
    return parts[0] if len(parts) == 3 and all(p.isdigit() for p in parts) else None


def stream_speakers(fileobj, n_speakers: int, per_speaker: int = UTTS_PER_SPEAKER) -> dict:
    """{speaker: [flac bytes, ...]} for the first n_speakers speakers in archive order, up to per_speaker utterances
    each. Stops reading once every one of them has per_speaker utterances, or when speaker n_speakers + 1 starts."""
    got: dict[str, list[bytes]] = {}
    with tarfile.open(fileobj=fileobj, mode="r|gz") as t:
        for m in t:
            spk = speaker_of(m.name) if m.isfile() else None
            if spk is None:
                continue
            if spk not in got:
                if len(got) >= n_speakers:
                    break  # the archive is grouped by speaker: the wanted ones are done
                got[spk] = []
            if len(got[spk]) < per_speaker:
                got[spk].append(t.extractfile(m).read())
            if len(got) >= n_speakers and all(len(v) >= per_speaker for v in got.values()):
                break
    return got


def fetch_speakers(n_speakers: int, per_speaker: int = UTTS_PER_SPEAKER, fetch=http_open, urls=URLS,
                   max_bytes: int = MAX_DOWNLOAD) -> dict:
    err = None
    for url in urls:
        budget = {"used": 0, "max": max_bytes}
        try:
            with fetch(url) as r:
                got = stream_speakers(io.BufferedReader(_Counted(r, budget)), n_speakers, per_speaker)
            print(f"{url}: {len(got)} speakers, {budget['used'] >> 20} MB read", file=sys.stderr)
            if len(got) >= n_speakers:
                return got
            err = DownloadError(f"only {len(got)} speakers, asked for {n_speakers}")
        except (OSError, http.client.HTTPException, tarfile.TarError, DownloadError) as e:
            err = e
            print(f"{url}: {e}", file=sys.stderr)
    raise DownloadError(f"no LibriSpeech mirror worked: {err}")


def decode(data: bytes):
    """flac/wav bytes -> float32 mono 16 kHz numpy array."""
    import numpy as np
    import soundfile as sf

    x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(sr, SR)
        x = resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def cut(n_samples: int, rng: random.Random) -> list[tuple[int, int]]:
    """Split [0, n_samples) into consecutive pieces whose lengths follow BANDS; a tail < MIN_PIECE joins the last."""
    out, pos = [], 0
    while pos < n_samples:
        r, acc = rng.random() * sum(w for *_, w in BANDS), 0.0
        lo, hi = BANDS[-1][:2]
        for blo, bhi, w in BANDS:
            acc += w
            if r < acc:
                lo, hi = blo, bhi
                break
        end = min(n_samples, pos + int(rng.uniform(lo, hi) * SR))
        if n_samples - end < MIN_PIECE * SR:
            end = n_samples
        out.append((pos, end))
        pos = end
    return out


def conversation(utts: dict, rng: random.Random, turns_per_speaker: int = 8) -> list[tuple[str, object]]:
    """utts {speaker: [audio, ...]} -> [(speaker, piece)] in meeting order: each turn is one speaker's next utterance
    (cycled) cut into pieces, of which the turn keeps 1-3; the next turn is always another speaker."""
    spk = sorted(utts)
    nxt = {s: 0 for s in spk}
    out, last = [], None
    for _ in range(turns_per_speaker * len(spk)):
        s = rng.choice([x for x in spk if x != last] or spk)
        a = utts[s][nxt[s] % len(utts[s])]
        nxt[s] += 1
        pieces = cut(len(a), rng)
        for b, e in pieces[:rng.randint(1, 3)]:
            out.append((s, a[b:e]))
        last = s
    return out


def write_clip(out: Path, items) -> Path:
    import soundfile as sf

    out.mkdir(parents=True, exist_ok=True)
    labels = out / "labels.csv"
    with open(labels, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "speaker"])
        for i, (s, a) in enumerate(items, 1):
            name = f"c{i:04d}.wav"
            sf.write(out / name, a, SR, subtype="PCM_16")
            w.writerow([name, s])
    return labels


def build(out: Path, n_speakers: int, seed: int = 1256, fetch=http_open, urls=URLS) -> dict:
    raw = fetch_speakers(n_speakers, fetch=fetch, urls=urls)
    utts = {s: [decode(b) for b in v] for s, v in raw.items()}
    items = conversation(utts, random.Random(seed))
    write_clip(Path(out), items)
    secs = [len(a) / SR for _, a in items]
    return {"speakers": len(utts), "pieces": len(items), "minutes": round(sum(secs) / 60, 1),
            "short_1_2s": sum(1 for x in secs if 1.0 <= x < 2.0)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--speakers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1256)
    a = ap.parse_args(argv)
    r = build(a.out, a.speakers, a.seed)
    print(f"clip: {r['speakers']} speakers, {r['pieces']} pieces ({r['short_1_2s']} of 1-2 s), {r['minutes']} min "
          f"-> {a.out / 'labels.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
