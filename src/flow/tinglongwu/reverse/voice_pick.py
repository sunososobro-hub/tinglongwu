"""#319: `--voice male|female` for the Vosk Russian built-in voices.

Upstream (alphacep/vosk-tts README) only says the multi-speaker Russian model has ids 0-4, "3 females and 2 males",
with no id -> gender table, and the table may differ between model versions (0.7 vs 0.9). So the mapping is measured,
not hard-coded: at startup each id speaks PROBE_TEXT once, its median pitch (F0) is measured, and ids below
F0_SPLIT_HZ count as male, the rest as female (adult male speech is ~85-155 Hz, female ~165-255 Hz). `--voice` takes
the lowest id of the asked gender; `--speaker N` still wins when both are given.
"""
from __future__ import annotations

import numpy as np

VOICES = ("male", "female")
VOSK_RU_IDS = (0, 1, 2, 3, 4)  # vosk-model-tts-ru-0.7-multi: 0-4 (upstream: 3 female, 2 male)
F0_SPLIT_HZ = 165.0  # male < 165 Hz <= female
F0_MIN_HZ, F0_MAX_HZ = 60.0, 400.0
PROBE_TEXT = "Добрый день, как у вас дела?"


def median_f0(pcm, sr: int, frame_s: float = 0.04) -> float | None:
    """Median pitch (Hz) over the voiced frames of mono float audio (autocorrelation per frame), None if unvoiced."""
    x = np.asarray(pcm, dtype=np.float64).reshape(-1)
    n = int(sr * frame_s)
    if n < 8 or x.size < n:
        return None
    lo, hi = int(sr / F0_MAX_HZ), min(int(sr / F0_MIN_HZ), n - 1)
    frames = [x[i:i + n] for i in range(0, x.size - n + 1, n // 2)]
    floor = 0.1 * max(float(np.sqrt(np.mean(f * f))) for f in frames)  # skip pauses: < 10% of the loudest frame
    f0s = []
    for f in frames:
        f = f - f.mean()
        if float(np.sqrt(np.mean(f * f))) < floor or floor == 0:
            continue
        ac = np.correlate(f, f, mode="full")[n - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / (n - np.arange(n)) * n  # unbiased: long lags are not penalised for overlapping less
        seg = ac[lo:hi + 1] / ac[0]
        peak = float(seg.max())
        if peak < 0.3:  # weak periodicity: unvoiced (fricatives, noise)
            continue
        # the true period peaks at ~1.0, but so do its multiples (sub-octaves) and a strong 2nd harmonic gets close
        # at half the period: take the shortest lag within 5% of the best one, so 2*T wins over a weaker T/2
        k = int(np.argmax(seg >= 0.95 * peak))
        while k + 1 < seg.size and seg[k + 1] > seg[k]:  # climb to the local maximum
            k += 1
        f0s.append(sr / (lo + k))
    return float(np.median(f0s)) if f0s else None


def gender(f0: float | None) -> str | None:
    if f0 is None:
        return None
    return "male" if f0 < F0_SPLIT_HZ else "female"


def pick_voice(voice: str, probe, ids=VOSK_RU_IDS) -> tuple[int, dict]:
    """(speaker id, {id: median F0}) for `voice`; probe(id) -> (pcm, sr). Lowest id of that gender wins.
    Raises ValueError when no id measures as that gender."""
    if voice not in VOICES:
        raise ValueError(f"--voice takes {' or '.join(VOICES)}, got {voice!r}")
    table = {}
    for sid in ids:
        pcm, sr = probe(sid)
        table[sid] = median_f0(pcm, sr)
    hits = [sid for sid in ids if gender(table[sid]) == voice]
    if not hits:
        raise ValueError(f"no {voice} voice found (measured median pitch: {format_table(table)})")
    return hits[0], table


def format_table(table: dict) -> str:
    return ", ".join(f"{sid}={'?' if f is None else f'{f:.0f}Hz ' + gender(f)}" for sid, f in table.items())


def client_probe(tts, text: str = PROBE_TEXT):
    """probe(id) for pick_voice that speaks `text` with a running TTSClient (vosk) and returns its audio."""
    def probe(sid: int):
        old, tts.speaker = tts.speaker, sid
        try:
            parts, sr = [], 24000
            for h, data in tts.speak(text):
                if h["t"] == "chunk" and data:
                    sr = h.get("sr", sr)
                    parts.append(np.frombuffer(data, dtype=np.float32))
                elif h["t"] == "error":
                    raise RuntimeError(f"TTS worker: {h.get('msg')}")
            return (np.concatenate(parts) if parts else np.zeros(0, np.float32)), sr
        finally:
            tts.speaker = old
    return probe
