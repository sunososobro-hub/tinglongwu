"""#123: append one JSON line per smoke run to a history file and draw trend charts (inline SVG) from it."""
from __future__ import annotations

import datetime
import html
import json
import subprocess
import sys

W, H, PAD = 560, 180, 40
COLOURS = ("#3b6fb6", "#c0392b", "#27ae60", "#8e44ad", "#e67e22")
# metric key -> (title, unit); the first three are per language pair, the others single series
METRICS = (("chrf", "chrF (x100)"), ("mt_median_s", "MT median latency (s)"), ("asr_rtf", "ASR real-time factor"),
           ("peak_vram_mib", "Peak VRAM (MiB)"), ("hallucination", "Noise hallucination count"))


def git_commit(run=subprocess.run) -> str | None:
    try:
        return run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:  # noqa: BLE001 - not a git checkout
        return None


def make_entry(rep: dict, commit: str | None = None, date: str | None = None) -> dict:
    langs = rep["languages"]
    nz = rep.get("noise")
    return {
        "date": date or datetime.date.today().isoformat(), "commit": commit,
        "chrf": {lg["pair"]: None if lg["chrf"] is None else round(float(lg["chrf"]), 2) for lg in langs},
        "mt_median_s": {lg["pair"]: lg["mt_median_s"] for lg in langs},
        "asr_rtf": {lg["pair"]: lg["asr_rtf"] for lg in langs},
        "peak_vram_mib": rep.get("peak_vram_mib"),
        "hallucination": None if not nz else nz["count"],
    }


def append(path, rep: dict, commit: str | None = None, date: str | None = None) -> dict:
    entry = make_entry(rep, commit, date)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load(path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as e:
                    raise ValueError(f"{path}:{n}: not JSON ({e})") from e
    return rows


def _series(rows: list[dict], key: str) -> dict[str, list]:
    """series name -> one value (or None) per history row."""
    names: list[str] = []
    for r in rows:
        v = r.get(key)
        if isinstance(v, dict):
            names += [k for k in v if k not in names]
        elif "" not in names:
            names.append("")
    out = {}
    for n in names:
        out[n] = [(r.get(key) or {}).get(n) if isinstance(r.get(key), dict) else (r.get(key) if n == "" else None) for r in rows]
    return out


def _svg(title: str, rows: list[dict], series: dict[str, list]) -> str:
    vals = [v for s in series.values() for v in s if v is not None]
    out = [f'<h2>{html.escape(title)}</h2><svg width="{W}" height="{H}" role="img" aria-label="{html.escape(title)}">']
    if not vals:
        return "".join(out) + f'<text x="{PAD}" y="{H // 2}" class="na">no data</text></svg>'
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi + 1, lo - 1 if lo > 0 else lo
    n = len(rows)
    px = lambda i: PAD + ((W - 2 * PAD) * i / (n - 1) if n > 1 else (W - 2 * PAD) / 2)  # noqa: E731
    py = lambda v: H - PAD - (H - 2 * PAD) * (v - lo) / (hi - lo)  # noqa: E731
    out.append(f'<line x1="{PAD}" y1="{H - PAD}" x2="{W - PAD}" y2="{H - PAD}" class="axis"/>'
               f'<text x="2" y="{PAD}">{hi:.2f}</text><text x="2" y="{H - PAD}">{lo:.2f}</text>'
               f'<text x="{PAD}" y="{H - 8}">{html.escape(str(rows[0].get("date", "")))}</text>'
               f'<text x="{W - PAD - 60}" y="{H - 8}">{html.escape(str(rows[-1].get("date", "")))}</text>')
    for k, (name, s) in enumerate(series.items()):
        col = COLOURS[k % len(COLOURS)]
        pts = [(px(i), py(v), v) for i, v in enumerate(s) if v is not None]
        if len(pts) > 1:
            out.append(f'<polyline fill="none" stroke="{col}" stroke-width="2" points="'
                       + " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts) + '"/>')
        for x, y, v in pts:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{col}"/>'
                       f'<text x="{x + 4:.1f}" y="{y - 4:.1f}" fill="{col}">{v:.2f}</text>')
        if name:
            out.append(f'<text x="{W - PAD - 60}" y="{14 + 14 * k}" fill="{col}">{html.escape(name)}</text>')
    return "".join(out) + "</svg>"


def render_trend(rows: list[dict]) -> str:
    parts = [_svg(title, rows, _series(rows, key)) for key, title in METRICS]
    return ("<!doctype html><html><head><meta charset='utf-8'><title>tlwu eval trend</title><style>"
            "body{font:14px sans-serif;margin:16px;background:#fff;color:#222}svg text{font:12px sans-serif}"
            ".axis{stroke:#888}.na{fill:#888}</style></head><body><h1>tinglongWu eval trend</h1>"
            f"<p>{len(rows)} run(s).</p>" + "".join(parts) + "</body></html>")


def trend_main(history_path: str, out: str) -> int:
    try:
        rows = load(history_path)
    except (OSError, ValueError) as e:
        print(f"tlwu-eval: {e}", file=sys.stderr)
        return 2
    if not rows:
        print(f"tlwu-eval: {history_path} has no runs yet", file=sys.stderr)
        return 2
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_trend(rows))
    print(f"trend: {out}")
    return 0
