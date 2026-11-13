"""#347: the measured-latency tables in docs/latency.md and docs/img/latency_tiers.svg, generated from committed runs.

No number here is typed by hand: every figure comes from a run file in eval/latency/, and each run file wraps the
records of one `tlwu --file CLIP --paced --latency-jsonl L.jsonl` session (#323: seconds from the end of speech to
the translated line shown, field `total`) together with the machine and settings it ran on.

  python scripts/make_latency_doc.py pack L.jsonl --tier mid-gpu --hardware "RTX 3050 8 GB" \\
      --model large-v3-turbo --engine hymt --preset meeting --pair en->zh-TW [--date YYYY-MM-DD]
      -> eval/latency/<date>_<tier>_<src>_<preset>.json
  python scripts/make_latency_doc.py           rewrite the generated block of docs/latency.md and the chart
  python scripts/make_latency_doc.py --check   exit 1 when either is stale (the doc test runs this)

A record may carry `draft` (seconds from segment audio start to the first translated draft line, --stream); the
draft column shows its p50 when any record has it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tlwu_eval.scoring import percentile  # noqa: E402  nearest rank: every p50/p90 is a real measurement

RUNS = ROOT / "eval" / "latency"
DOC = ROOT / "docs" / "latency.md"
SVG = ROOT / "docs" / "img" / "latency_tiers.svg"
START, END = "<!-- LATENCY:START (python scripts/make_latency_doc.py) -->", "<!-- LATENCY:END -->"
TIER_ORDER = ("cpu-low", "mid-gpu", "high")  # tlwu_eval.tiers.TIERS (#120)
MIN_N = 30  # #347 acceptance: n >= 30 utterances per cell; smaller cells are shown but marked indicative
META = ("date", "tier", "hardware", "model", "engine", "preset", "pair")


def pack(jsonl: Path, meta: dict, out_dir: Path = RUNS) -> Path:
    recs = [json.loads(line) for line in jsonl.read_text("utf-8").splitlines() if line.strip()]
    recs = [{k: r[k] for k in ("total", "vad", "asr", "merge", "mt", "draft") if r.get(k) is not None} for r in recs]
    if not recs:
        raise ValueError(f"{jsonl}: no latency records")
    src = meta["pair"].split("->")[0]
    out = out_dir / f"{meta['date']}_{meta['tier']}_{src}_{meta['preset']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**meta, "records": recs}, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return out


def load_runs(run_dir: Path = RUNS) -> list[dict]:
    runs = []
    for p in sorted(run_dir.glob("*.json")) if run_dir.is_dir() else []:
        r = json.loads(p.read_text("utf-8"))
        missing = [k for k in META if not r.get(k)]
        if missing or not r.get("records"):
            raise ValueError(f"{p.name}: missing {', '.join(missing) or 'records'}")
        runs.append(r)
    return runs


def cells(runs: list[dict]) -> list[dict]:
    """One cell per (tier, hardware, pair, preset, model, engine): the newest date's runs, pooled."""
    by = {}
    for r in runs:
        key = (r["tier"], r["hardware"], r["pair"], r["preset"], r["model"], r["engine"])
        if key not in by or r["date"] > by[key]["date"]:
            by[key] = {"date": r["date"], "records": []}
        if r["date"] == by[key]["date"]:
            by[key]["records"] += r["records"]
    out = []
    for key, v in by.items():
        tot = [x["total"] for x in v["records"]]
        drafts = [x["draft"] for x in v["records"] if "draft" in x]
        out.append(dict(zip(("tier", "hardware", "pair", "preset", "model", "engine"), key), date=v["date"],
                        n=len(tot), totals=sorted(tot), p50=percentile(tot, 50), p90=percentile(tot, 90),
                        draft=percentile(drafts, 50) if drafts else None))
    rank = {t: i for i, t in enumerate(TIER_ORDER)}
    return sorted(out, key=lambda c: (rank.get(c["tier"], len(rank)), c["tier"], c["hardware"], c["pair"], c["preset"]))


def table(cs: list[dict]) -> str:
    if not cs:
        return "No measured runs are committed yet (eval/latency/ is empty), so there is no table and no chart yet."
    head = ("| Tier | Hardware | Pair | Preset | Model / engine | Date | n | p50 | p90 | Draft first line p50 |\n"
            "|---|---|---|---|---|---|---|---|---|---|")
    rows = [f"| {c['tier']} | {c['hardware']} | {c['pair']} | {c['preset']} | {c['model']} / {c['engine']} | "
            f"{c['date']} | {c['n']}{'' if c['n'] >= MIN_N else ' (indicative)'} | {c['p50']:.2f} s | "
            f"{c['p90']:.2f} s | {'not recorded' if c['draft'] is None else format(c['draft'], '.2f') + ' s'} |"
            for c in cs]
    return "\n".join([head, *rows, "", "![Latency distribution per tier](img/latency_tiers.svg)"])


def block(cs: list[dict]) -> str:
    return (f"{START}\n## Measured runs (generated)\n\n"
            "Latency here is end of speech to the translated subtitle shown (#323 `--latency-jsonl`, field `total`), "
            "from a real-time paced clip. p50 / p90 are nearest-rank percentiles; cells with fewer than "
            f"{MIN_N} utterances are marked indicative. Do not edit this block by hand: add a run with "
            "`python scripts/make_latency_doc.py pack ...` and rerun the script.\n\n" + table(cs) + f"\n{END}")


def render_doc(doc: str, cs: list[dict]) -> str:
    if START in doc and END in doc:
        a, rest = doc.split(START, 1)
        return a + block(cs) + rest.split(END, 1)[1]
    head, sep, rest = doc.partition("\n## ")  # first time: right before the first dated section
    return head.rstrip("\n") + "\n\n" + block(cs) + "\n" + (sep + rest if sep else "\n")


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def chart(cs: list[dict]) -> str:
    """One row per cell: a dot per utterance, a bar at p50 and at p90 (seconds, shared axis)."""
    x0, w_axis, rh, y0 = 330, 460, 30, 46
    mx = max([4.0] + [c["totals"][-1] for c in cs])
    sx = lambda s: x0 + s / mx * w_axis  # noqa: E731
    w, h = x0 + w_axis + 30, y0 + rh * max(len(cs), 1) + 44
    font = 'font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif"'
    t = lambda x, y, s, a="start", size=13: (f'<text x="{x:.1f}" y="{y}" text-anchor="{a}" fill="#6e7781" '  # noqa: E731
                                             f'font-size="{size}">{esc(s)}</text>\n')
    b = t(10, 22, "End of speech to subtitle (s): dots = utterances, | = p50, ! = p90", size=13)
    if not cs:
        b += t(10, y0 + 18, "no measured runs committed yet")
    for i, c in enumerate(cs):
        y = y0 + rh * i
        b += t(10, y + 18, f"{c['tier']} {c['pair']} {c['preset']} (n={c['n']})")
        b += "".join(f'<circle cx="{sx(v):.1f}" cy="{y + 13}" r="3" fill="#388bfd" fill-opacity="0.45"/>\n'
                     for v in c["totals"])
        for v, col in ((c["p50"], "#2da44e"), (c["p90"], "#bf8700")):
            b += f'<rect x="{sx(v) - 1.5:.1f}" y="{y + 2}" width="3" height="22" fill="{col}"/>\n'
    ya = y0 + rh * max(len(cs), 1) + 8
    for s in range(int(mx) + 1):
        b += f'<line x1="{sx(s):.1f}" y1="{y0 - 4}" x2="{sx(s):.1f}" y2="{ya}" stroke="#7d8590" stroke-opacity="0.3"/>\n'
        b += t(sx(s), ya + 16, f"{s} s", "middle", 12)
    title = "Measured end-to-end latency per hardware tier"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="{title}" {font}>\n<title>{title}</title>\n{b}</svg>\n')


def main(argv=None, runs_dir: Path = RUNS, doc_path: Path = DOC, svg_path: Path = SVG) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["pack"]:
        p = argparse.ArgumentParser(prog="make_latency_doc.py pack")
        p.add_argument("jsonl", type=Path)
        for k in META[1:]:
            p.add_argument(f"--{k}", required=True)
        p.add_argument("--date", default=datetime.date.today().isoformat())
        a = p.parse_args(argv[1:])
        print("wrote", pack(a.jsonl, {k: getattr(a, k) for k in META}, runs_dir))
        return 0
    check = argv == ["--check"]
    cs = cells(load_runs(runs_dir))
    doc, svg = render_doc(doc_path.read_text("utf-8"), cs), chart(cs)
    if check:
        stale = [p.name for p, new in ((doc_path, doc), (svg_path, svg))
                 if not p.is_file() or p.read_text("utf-8") != new]
        if stale:
            print("stale:", ", ".join(stale), "- run: python scripts/make_latency_doc.py", file=sys.stderr)
        return 1 if stale else 0
    doc_path.write_text(doc, "utf-8", newline="\n")
    svg_path.write_text(svg, "utf-8", newline="\n")
    print("wrote", doc_path.name, svg_path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
