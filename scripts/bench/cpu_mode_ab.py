#!/usr/bin/env python3
"""#781 (#745 part 2): CPU-mode A/B table. Compares two `tlwu_eval --smoke` reports run with --device cpu:
A = current defaults, B = the CPU-mode settings from #745 part 1. Prints one Markdown table: p50 latency,
chrF and WER/CER per pair and overall, plus the deltas against the #745 targets.

  python scripts/bench/cpu_mode_ab.py --run --data eval_data/smoke --out-dir ab \
      --base-args "--model small --engine hymt2-cpu" --cpu-args "--model base --engine hymt2-cpu"
  python scripts/bench/cpu_mode_ab.py ab/base.json ab/cpu.json     # table from two existing reports

--run runs `python -m tlwu_eval --smoke --device cpu --no-noise --latency-jsonl` twice (the #569 eval set, see
docs/testing.md) and writes OUT_DIR/base.json, cpu.json and the two .jsonl latency files.
Targets (#745): p50 drop >= 30%, chrF drop <= 1.0, WER rise <= 2 points. The real numbers are printed either way.
"""
from __future__ import annotations

import argparse
import json
import shlex
import statistics
import subprocess
import sys
from pathlib import Path

TARGETS = {"p50_drop_pct_min": 30.0, "chrf_drop_max": 1.0, "wer_rise_pts_max": 2.0}


def load_jsonl(path) -> list[dict]:
    return [json.loads(ln) for ln in Path(path).read_text("utf-8").splitlines() if ln.strip()]


def latencies(rep: dict, jsonl: list[dict] | None = None) -> dict[str, list[float]]:
    """pair -> per-clip end-to-end seconds (ASR+MT). From a --latency-jsonl list ({pair, total}) when given,
    else from the report's clips (latency_s)."""
    out: dict[str, list[float]] = {}
    if jsonl is not None:
        for r in jsonl:
            out.setdefault(r["pair"], []).append(float(r["total"]))
        return out
    for lg in rep.get("languages", []):
        out[lg["pair"]] = [float(c["latency_s"]) for c in lg.get("clips", []) if c.get("latency_s") is not None]
    return out


def summarize(rep: dict, jsonl: list[dict] | None = None) -> dict:
    """{pair: {p50_s, chrf, wer}, '_all': {...}}; wer = ASR error rate x100 (points); chrf None when unmeasured."""
    lat = latencies(rep, jsonl)
    if jsonl is not None and set(lat) != {lg["pair"] for lg in rep.get("languages", [])}:
        raise ValueError("latency jsonl and report cover different pairs (stale .jsonl?): only in jsonl "
                         + (", ".join(sorted(set(lat) - {lg["pair"] for lg in rep.get("languages", [])})) or "-"))
    res: dict[str, dict] = {}
    for lg in rep.get("languages", []):
        v = lat.get(lg["pair"], [])
        res[lg["pair"]] = {"p50_s": statistics.median(v) if v else None, "chrf": lg.get("chrf"),
                           "wer": 100 * lg["error_rate"] if lg.get("error_rate") is not None else None}
    allv = [x for v in lat.values() for x in v]
    chrfs = [r["chrf"] for r in res.values() if r["chrf"] is not None]
    wers = [r["wer"] for r in res.values() if r["wer"] is not None]
    res["_all"] = {"p50_s": statistics.median(allv) if allv else None,
                   "chrf": statistics.fmean(chrfs) if chrfs else None,
                   "wer": statistics.fmean(wers) if wers else None}
    return res


def _f(v, nd=2):
    return "-" if v is None else f"{v:.{nd}f}"


def _delta(a, b):
    return None if a is None or b is None else b - a


def verdict(a: dict, b: dict) -> dict:
    """Overall deltas of B (CPU mode) against A (defaults) and whether each #745 target is met (None = no data)."""
    x, y = a["_all"], b["_all"]
    drop = None if not x["p50_s"] or y["p50_s"] is None else round(100 * (x["p50_s"] - y["p50_s"]) / x["p50_s"], 6)
    dc, dw = _delta(y["chrf"], x["chrf"]), _delta(x["wer"], y["wer"])  # chrF drop = A - B; WER rise = B - A
    # reports carry 2-4 decimals: round away float noise so an inclusive limit (e.g. 0.05 -> 0.07 = 2.0) is met
    dc, dw = (None if v is None else round(v, 6) for v in (dc, dw))
    return {"p50_drop_pct": drop, "chrf_drop": dc, "wer_rise_pts": dw,
            "p50_ok": None if drop is None else drop >= TARGETS["p50_drop_pct_min"],
            "chrf_ok": None if dc is None else dc <= TARGETS["chrf_drop_max"],
            "wer_ok": None if dw is None else dw <= TARGETS["wer_rise_pts_max"]}


def check_pairs(a: dict, b: dict) -> None:
    """Both runs must cover the same language pairs, else the overall numbers compare different clip sets."""
    pa, pb = set(a) - {"_all"}, set(b) - {"_all"}
    if pa != pb:
        raise ValueError("reports cover different pairs: only in defaults " + (", ".join(sorted(pa - pb)) or "-")
                         + "; only in CPU mode " + (", ".join(sorted(pb - pa)) or "-"))


def table(a: dict, b: dict) -> str:
    lines = ["| pair | p50 s (defaults) | p50 s (CPU mode) | chrF (defaults) | chrF (CPU mode) | WER % (defaults) | WER % (CPU mode) |",
             "|---|---|---|---|---|---|---|"]
    pairs = [p for p in a if p != "_all" and p in b]
    for p in pairs + ["_all"]:
        x, y = a[p], b[p]
        name = "**all**" if p == "_all" else p
        lines.append(f"| {name} | {_f(x['p50_s'], 3)} | {_f(y['p50_s'], 3)} | {_f(x['chrf'])} | {_f(y['chrf'])} "
                     f"| {_f(x['wer'])} | {_f(y['wer'])} |")
    v = verdict(a, b)
    ok = lambda k: "n/a" if v[k] is None else ("met" if v[k] else "missed")  # noqa: E731
    lines += ["", f"p50 drop {_f(v['p50_drop_pct'], 1)}% (target >= {TARGETS['p50_drop_pct_min']:g}%: {ok('p50_ok')}); "
                  f"chrF drop {_f(v['chrf_drop'])} (target <= {TARGETS['chrf_drop_max']:g}: {ok('chrf_ok')}); "
                  f"WER rise {_f(v['wer_rise_pts'])} points (target <= {TARGETS['wer_rise_pts_max']:g}: {ok('wer_ok')})"]
    return "\n".join(lines)


def run_eval(extra: str, data: str, out_json: Path, out_jsonl: Path) -> None:
    cmd = [sys.executable, "-m", "tlwu_eval", "--smoke", "--device", "cpu", "--no-noise", "--data", data,
           "--out", str(out_json), "--latency-jsonl", str(out_jsonl), *shlex.split(extra)]
    subprocess.run(cmd, check=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("reports", nargs="*", metavar="BASE.json CPU.json")
    p.add_argument("--run", action="store_true", help="run tlwu_eval twice on CPU first")
    p.add_argument("--data", default="eval_data/smoke")
    p.add_argument("--out-dir", default="ab")
    p.add_argument("--base-args", default="", help="extra tlwu_eval args for the defaults run")
    p.add_argument("--cpu-args", default="", help="extra tlwu_eval args for the CPU-mode run")
    a = p.parse_args(argv)
    if a.run and not a.cpu_args.strip():
        p.error("--run needs --cpu-args; without it the CPU-mode run repeats the defaults run")
    d = Path(a.out_dir)
    if a.run:
        d.mkdir(parents=True, exist_ok=True)
        for name, extra in (("base", a.base_args), ("cpu", a.cpu_args)):
            (d / f"{name}.jsonl").unlink(missing_ok=True)
            run_eval(extra, a.data, d / f"{name}.json", d / f"{name}.jsonl")
        paths = [d / "base.json", d / "cpu.json"]
    elif len(a.reports) == 2:
        paths = [Path(x) for x in a.reports]
    else:
        p.error("give BASE.json CPU.json, or --run")
    try:
        reps = [json.loads(x.read_text("utf-8")) for x in paths]
        jl = [load_jsonl(x.with_suffix(".jsonl")) if x.with_suffix(".jsonl").exists() else None for x in paths]
        sa, sb = summarize(reps[0], jl[0]), summarize(reps[1], jl[1])
        check_pairs(sa, sb)
        print(table(sa, sb))
    except (OSError, ValueError, KeyError) as e:
        print(f"cpu_mode_ab: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
