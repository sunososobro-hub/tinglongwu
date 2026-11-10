"""Scores for the eval report (#57): every weight and threshold lives in this one file.

Each metric is turned into a 0-100 score; the overall score is the weighted mean of the metrics that were
measured (weights are re-normalised when one is missing, e.g. no TTS or no silence clips in a smoke run).
Pure Python, no dependencies: raw measurements come from the runner, this file only scores them.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Weights: owner spec v2 on #57 (2026-11-10), table "Metrics".
WEIGHTS = {"asr": 25, "mt": 25, "latency": 20, "hallucination": 15, "resources": 10, "lang_id": 5}

# Colours: owner spec v2 on #57 (">=80 green, 60-79 yellow, <60 red").
GREEN_MIN, YELLOW_MIN = 80, 60

# Latency, end of speech -> subtitle: owner spec v2 on #57 ("1 s = 100, >=4 s = 0"), linear in between.
LATENCY_BEST_S, LATENCY_WORST_S = 1.0, 4.0
# avg and p90 count equally: the spec lists both without a split; p90 is what a listener notices as "lag".
LATENCY_AVG_SHARE = 0.5

# Resources, peak use / machine total: 100 up to half of the machine, 0 when everything is used. Design
# choice for #57 (no owner number): a model that needs more than half of RAM/VRAM leaves no room for the
# meeting app + browser that run next to tlwu.
RESOURCE_FREE_FRACTION, RESOURCE_FULL_FRACTION = 0.5, 1.0

CHAR_LANGS = {"zh", "ja"}  # scored by characters (CER); everything else by words (WER) - spec v2 table
CHRF_ORDER, CHRF_BETA = 6, 2.0  # chrF2 with character 1..6-grams = the sacreBLEU default (Popovic 2015)


def clamp(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


def linear(x: float, best: float, worst: float) -> float:
    """100 at `best`, 0 at `worst`, straight line in between (either direction)."""
    return clamp(100.0 * (worst - x) / (worst - best))


# ---------------------------------------------------------------- raw metrics
def tokenize(text: str, lang: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(c for c in text if not unicodedata.category(c).startswith(("P", "S")))
    return [c for c in text if not c.isspace()] if lang in CHAR_LANGS else text.split()


def edit_distance(ref: list, hyp: list) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def error_rate(pairs, lang: str) -> float:
    """Corpus WER (CER for zh/ja): total edits / total reference units over (reference, hypothesis) pairs."""
    errs = units = 0
    for ref, hyp in pairs:
        r = tokenize(ref, lang)
        errs += edit_distance(r, tokenize(hyp, lang))
        units += len(r)
    if not units:
        raise ValueError("no reference text")
    return errs / units


def _ngrams(text: str, n: int) -> Counter:
    s = re.sub(r"\s+", "", text)
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def chrf(pairs) -> float:
    """Corpus chrF2 (0-100) over (reference, hypothesis) pairs: character n-gram F-score, whitespace ignored."""
    f_sum, orders = 0.0, 0
    for n in range(1, CHRF_ORDER + 1):
        match = hyp_n = ref_n = 0
        for ref, hyp in pairs:
            r, h = _ngrams(ref, n), _ngrams(hyp, n)
            match += sum((r & h).values())
            ref_n += sum(r.values())
            hyp_n += sum(h.values())
        if not ref_n or not hyp_n:
            continue  # sentences shorter than n on one side: this order says nothing
        orders += 1
        p, rc = match / hyp_n, match / ref_n
        b2 = CHRF_BETA ** 2
        f_sum += (1 + b2) * p * rc / (b2 * p + rc) if p + rc else 0.0
    return 100.0 * f_sum / orders if orders else 0.0


def percentile(values, q: float) -> float:
    """Nearest-rank percentile (q in 0..100) - no interpolation, so a report value is a real measurement."""
    v = sorted(values)
    if not v:
        raise ValueError("no values")
    return v[max(0, min(len(v) - 1, -(-len(v) * q // 100) - 1))] if q > 0 else v[0]


# ---------------------------------------------------------------- 0-100 scores
def asr_score(rate: float) -> float:
    """WER/CER 0 -> 100, 1.0 (every unit wrong) or worse -> 0."""
    return clamp(100.0 * (1.0 - rate))


def mt_score(chrf_value: float) -> float:
    return clamp(chrf_value)  # chrF is already 0-100, higher is better


def latency_score(latencies_s) -> float:
    lat = list(latencies_s)
    avg = sum(lat) / len(lat)
    p90 = percentile(lat, 90)
    return (LATENCY_AVG_SHARE * linear(avg, LATENCY_BEST_S, LATENCY_WORST_S)
            + (1 - LATENCY_AVG_SHARE) * linear(p90, LATENCY_BEST_S, LATENCY_WORST_S))


def hallucination_score(outputs) -> float:
    """outputs: what was emitted for each silence/noise clip. Score = share of clips with no text at all."""
    out = list(outputs)
    if not out:
        raise ValueError("no silence/noise clips")
    return 100.0 * sum(1 for t in out if not (t or "").strip()) / len(out)


def resources_score(peak_ram, total_ram, peak_vram=None, total_vram=None) -> float:
    """Worst of RAM and VRAM (the tighter one decides whether the machine copes). Units cancel out."""
    fr = [peak_ram / total_ram]
    if peak_vram is not None and total_vram:
        fr.append(peak_vram / total_vram)
    return min(linear(f, RESOURCE_FREE_FRACTION, RESOURCE_FULL_FRACTION) for f in fr)


def lang_id_score(pairs) -> float:
    """pairs: (expected language, detected language) per short clip."""
    p = list(pairs)
    if not p:
        raise ValueError("no clips")
    return 100.0 * sum(1 for want, got in p if want == got) / len(p)


def colour(score: float) -> str:
    return "green" if score >= GREEN_MIN else "yellow" if score >= YELLOW_MIN else "red"


def overall(scores: dict) -> dict:
    """scores: metric -> 0-100 or None (not measured). Returns the weighted overall and per-metric colours."""
    unknown = set(scores) - set(WEIGHTS)
    if unknown:
        raise ValueError(f"unknown metric(s): {sorted(unknown)}")
    have = {k: clamp(v) for k, v in scores.items() if v is not None}
    if not have:
        raise ValueError("no metric was measured")
    total = sum(WEIGHTS[k] for k in have)
    value = sum(WEIGHTS[k] * v for k, v in have.items()) / total
    return {"overall": round(value, 1), "colour": colour(value),
            "metrics": {k: {"score": round(v, 1), "weight": WEIGHTS[k], "colour": colour(v)} for k, v in have.items()},
            "missing": sorted(set(WEIGHTS) - set(have))}
