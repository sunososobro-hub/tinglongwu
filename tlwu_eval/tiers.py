"""#120 per-hardware-tier eval: presets and one summary per run (COMET, chrF, hallucination, latency).
Each tier runs on its own machine; `--chart` (tierchart.py) compares them. Scores x100, 2 decimals; latency in s."""
from __future__ import annotations

import statistics

from . import scoring

# Tier presets. Source: #120 ("CPU low: base, OPUS, drafts off; mid GPU: small/turbo + Hy-MT2; high") and the
# hardware probe (tinglongwu/hwprobe.py): CPU fast level = base + OPUS, GPU tier = Hy-MT2, GPU quality level =
# large-v3 next to Hy-MT2. The eval harness never runs drafts (--stream), so "drafts off" needs no flag.
# mid-gpu uses small (the owner's hallucination baseline model); pass --model large-v3-turbo for the turbo run.
TIERS = {
    "cpu-low": {"model": "base", "engine": "opus", "device": "cpu"},
    "mid-gpu": {"model": "small", "engine": "hymt", "device": "cuda"},
    "high": {"model": "large-v3", "engine": "hymt", "device": "cuda"},
}

# Current baseline, owner on #120: "small hallucinates on ru in 10 of 20" (first 20 FLEURS ru utterances).
BASELINE = {"label": "small ru (baseline)", "pair": "ru->zh", "count": 10, "total": 20}

# A speech clip counts as hallucinated when the ASR text is a stock subtitle phrase (tinglongwu.hallucination,
# the same filter the app uses) or has more errors than the reference has words/characters (error rate > 1.0:
# text that cannot come from the audio, e.g. a decoder loop). Design choice for #120: both are text the
# speaker never said; ordinary misrecognitions stay below 1.0 and are scored by WER/CER instead.
HALLUCINATION_ERROR_RATE = 1.0


def x100(v):
    return None if v is None else round(100.0 * v, 2)


def is_hallucinated(ref: str, asr: str, lang: str) -> bool:
    from tinglongwu.hallucination import is_hallucination

    if not (asr or "").strip():
        return False
    if is_hallucination(asr):
        return True
    return scoring.error_rate([(ref, asr)], lang) > HALLUCINATION_ERROR_RATE


def comet_scores(langs, model, gpus: int = 0) -> dict:
    """pair -> COMET system score x100. model: a loaded COMET model (Unbabel/wmt22-comet-da), .predict(...)."""
    out = {}
    for lg in langs:
        data = [{"src": c["ref_text"], "mt": c["mt"], "ref": c["ref_mt"]} for c in lg["clips"] if c.get("ref_mt")]
        if data:
            out[lg["pair"]] = x100(model.predict(data, batch_size=16, gpus=gpus, progress_bar=False).system_score)
    return out


def summarize(rep: dict, tier: str, comet: dict | None = None) -> dict:
    """The four #120 metrics for one tier run (per language pair and overall)."""
    pairs, lat, h_count, h_total = [], [], 0, 0
    for lg in rep["languages"]:
        src = lg["pair"].split("->")[0]
        hits = [c["asr"] for c in lg["clips"] if is_hallucinated(c["ref_text"], c["asr"], src)]
        h_count += len(hits)
        h_total += len(lg["clips"])
        lat += [c["latency_s"] for c in lg["clips"]]
        pairs.append({"pair": lg["pair"], "chrf": None if lg["chrf"] is None else round(lg["chrf"], 2),
                      "comet": (comet or {}).get(lg["pair"]),
                      "hallucination": {"count": len(hits), "total": len(lg["clips"]),
                                        "rate": x100(len(hits) / len(lg["clips"])), "texts": hits}})
    chrfs = [p["chrf"] for p in pairs if p["chrf"] is not None]
    comets = [p["comet"] for p in pairs if p["comet"] is not None]
    return {"tier": tier, "preset": TIERS.get(tier), "pairs": pairs,
            "chrf": round(statistics.fmean(chrfs), 2) if chrfs else None,
            "comet": round(statistics.fmean(comets), 2) if comets else None,
            "hallucination": {"count": h_count, "total": h_total, "rate": x100(h_count / h_total)},
            "latency_s": {"median": round(statistics.median(lat), 2), "p90": round(scoring.percentile(lat, 90), 2)},
            "baseline": BASELINE}


def summary_line(t: dict) -> str:
    h, lat, b = t["hallucination"], t["latency_s"], t["baseline"]
    return (f"tier {t['tier']}: chrF {t['chrf']} COMET {t['comet']} hallucination {h['count']}/{h['total']} ({h['rate']:.2f}) "
            f"latency median {lat['median']:.2f}s p90 {lat['p90']:.2f}s (baseline {b['label']} {b['count']}/{b['total']})")
