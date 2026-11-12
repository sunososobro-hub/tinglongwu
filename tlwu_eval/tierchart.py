"""#120 chart: tier summaries side by side in one self-contained HTML page (inline SVG), against the baseline."""
from __future__ import annotations

import html
import json
import sys

from .tiers import BASELINE

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
    base_rate = 100.0 * BASELINE["count"] / BASELINE["total"]
    ru = [next((p["hallucination"]["rate"] for p in s["pairs"] if p["pair"] == BASELINE["pair"]), None) for s in summaries]
    col = lambda f: [(s["tier"], f(s)) for s in summaries]  # noqa: E731
    rows = "".join(
        f"<tr><td>{html.escape(s['tier'])}</td><td>{s['chrf'] if s['chrf'] is not None else 'n/a'}</td>"
        f"<td>{s['comet'] if s['comet'] is not None else 'n/a'}</td>"
        f"<td>{s['hallucination']['count']}/{s['hallucination']['total']} ({s['hallucination']['rate']:.2f})</td>"
        f"<td>{s['latency_s']['median']:.2f} / {s['latency_s']['p90']:.2f}</td></tr>" for s in summaries)
    base = (BASELINE["label"], base_rate)
    parts = [_bars("chrF (x100)", col(lambda s: s["chrf"]), max_v=100),
             _bars("COMET (x100)", col(lambda s: s["comet"]), max_v=100),
             _bars("Hallucination, all languages (% of clips)", col(lambda s: s["hallucination"]["rate"]), "%", 100, base),
             _bars(f"Hallucination {BASELINE['pair']} (% of clips)", [(s["tier"], r) for s, r in zip(summaries, ru)], "%", 100, base),
             _bars("Latency median (s)", col(lambda s: s["latency_s"]["median"]), " s"),
             _bars("Latency p90 (s)", col(lambda s: s["latency_s"]["p90"]), " s")]
    return ("<!doctype html><html><head><meta charset='utf-8'><title>tlwu eval tiers</title><style>"
            "body{font:14px sans-serif;margin:16px;background:#fff;color:#222}svg text{font:12px sans-serif}"
            ".bar{fill:#3b6fb6}.base{stroke:#c0392b;stroke-dasharray:4 3}.base-t{fill:#c0392b}.na{fill:#888}"
            "td,th{padding:2px 8px;border-bottom:1px solid #ddd;text-align:left}</style></head><body>"
            "<h1>tinglongWu eval by hardware tier</h1><table><tr><th>tier</th><th>chrF</th><th>COMET</th>"
            f"<th>hallucination</th><th>latency median / p90 s</th></tr>{rows}</table>"
            f"<p>Baseline: {html.escape(BASELINE['label'])} {BASELINE['count']}/{BASELINE['total']} = {base_rate:.2f}%.</p>"
            + "".join(parts) + "</body></html>")


def main(p, args) -> int:
    """--chart OUT.html REPORT.json...: the tier summaries side by side."""
    out, reports = args[0], args[1:]
    if not out.endswith(".html") or not reports:
        p.error("--chart OUT.html REPORT.json [REPORT.json ...]")
    sums = []
    for path in reports:
        try:
            with open(path, encoding="utf-8") as f:
                sums.append(json.load(f)["tier"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            why = "has no tier summary (run it with --tier)" if isinstance(e, (KeyError, TypeError)) else f"cannot be read: {e}"
            print(f"tlwu-eval: {path} {why}", file=sys.stderr)
            return 2
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_chart(sums))
    print(f"chart: {out}")
    return 0
