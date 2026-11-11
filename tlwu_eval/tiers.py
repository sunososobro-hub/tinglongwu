"""Per-hardware-tier eval (#120): three presets, one summary per run (COMET, chrF, hallucination, latency) and
a chart that puts the tiers side by side against the current hallucination baseline.

    python -m tlwu_eval --smoke --tier cpu-low --per-lang 20 --data DIR --out cpu-low.json [--comet]
    python -m tlwu_eval --chart tiers.html cpu-low.json mid-gpu.json high.json

Each tier runs on its own machine (the nightly full job runs the line for its tier, then --chart). Scores are
x100 with two decimals (owner rule on #120); latency stays in seconds.
"""
from __future__ import annotations

import html
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


# ---------------------------------------------------------------- chart (self-contained HTML, inline SVG)
W, BAR_H, GAP, LABEL_W = 520, 22, 8, 150


def _bars(title: str, rows, unit: str = "", max_v: float | None = None, baseline=None) -> str:
    """rows: (label, value or None). baseline: (label, value) drawn as a dashed line."""
    vals = [v for _, v in rows if v is not None] + ([baseline[1]] if baseline else [])
    top = max_v or max(vals + [1e-9])
    span = W - LABEL_W - 70
    h = len(rows) * (BAR_H + GAP) + 30
    out = [f'<h2>{html.escape(title)}</h2><svg width="{W}" height="{h}" role="img" aria-label="{html.escape(title)}">']
    for i, (label, v) in enumerate(rows):
        y = i * (BAR_H + GAP)
        out.append(f'<text x="0" y="{y + 16}">{html.escape(label)}</text>')
        if v is None:
            out.append(f'<text x="{LABEL_W}" y="{y + 16}" class="na">n/a</text>')
            continue
        bw = max(1.0, span * v / top)
        out.append(f'<rect x="{LABEL_W}" y="{y}" width="{bw:.1f}" height="{BAR_H}" class="bar"/>'
                   f'<text x="{LABEL_W + bw + 4:.1f}" y="{y + 16}">{v:.2f}{unit}</text>')
    if baseline:
        x = LABEL_W + span * baseline[1] / top
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 22}" class="base"/>'
                   f'<text x="{x:.1f}" y="{h - 6}" class="base-t">{html.escape(baseline[0])} {baseline[1]:.2f}{unit}</text>')
    return "".join(out) + "</svg>"


def render_chart(summaries: list[dict]) -> str:
    names = [s["tier"] for s in summaries]
    base = BASELINE
    base_rate = 100.0 * base["count"] / base["total"]
    ru = []
    for s in summaries:
        p = next((p for p in s["pairs"] if p["pair"] == base["pair"]), None)
        ru.append((s["tier"], None if p is None else p["hallucination"]["rate"]))
    rows = "".join(
        f"<tr><td>{html.escape(s['tier'])}</td><td>{s['chrf'] if s['chrf'] is not None else 'n/a'}</td>"
        f"<td>{s['comet'] if s['comet'] is not None else 'n/a'}</td>"
        f"<td>{s['hallucination']['count']}/{s['hallucination']['total']} ({s['hallucination']['rate']:.2f})</td>"
        f"<td>{s['latency_s']['median']:.2f} / {s['latency_s']['p90']:.2f}</td></tr>" for s in summaries)
    parts = [
        _bars("chrF (x100)", [(n, s["chrf"]) for n, s in zip(names, summaries)], max_v=100),
        _bars("COMET (x100)", [(n, s["comet"]) for n, s in zip(names, summaries)], max_v=100),
        _bars("Hallucination, all languages (% of clips)",
              [(n, s["hallucination"]["rate"]) for n, s in zip(names, summaries)], "%", 100, (base["label"], base_rate)),
        _bars(f"Hallucination {base['pair']} (% of clips)", ru, "%", 100, (base["label"], base_rate)),
        _bars("Latency median (s)", [(n, s["latency_s"]["median"]) for n, s in zip(names, summaries)], " s"),
        _bars("Latency p90 (s)", [(n, s["latency_s"]["p90"]) for n, s in zip(names, summaries)], " s"),
    ]
    return ("<!doctype html><html><head><meta charset='utf-8'><title>tlwu eval tiers</title><style>"
            "body{font:14px sans-serif;margin:16px;background:#fff;color:#222}svg text{font:12px sans-serif}"
            ".bar{fill:#3b6fb6}.base{stroke:#c0392b;stroke-dasharray:4 3}.base-t{fill:#c0392b}.na{fill:#888}"
            "td,th{padding:2px 8px;border-bottom:1px solid #ddd;text-align:left}</style></head><body>"
            "<h1>tinglongWu eval by hardware tier</h1><table><tr><th>tier</th><th>chrF</th><th>COMET</th>"
            f"<th>hallucination</th><th>latency median / p90 s</th></tr>{rows}</table>"
            f"<p>Baseline: {html.escape(base['label'])} {base['count']}/{base['total']} = {base_rate:.2f}%.</p>"
            + "".join(parts) + "</body></html>")
