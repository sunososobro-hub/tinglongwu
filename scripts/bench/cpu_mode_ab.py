#!/usr/bin/env python3
"""#781 (#745 part 2): CPU-mode A/B table. Compares two `tlwu_eval --smoke` reports run with --device cpu:
A = current defaults, B = the CPU-mode settings from #745 part 1. Prints one Markdown table: p50 latency,
chrF and WER/CER per pair and overall, plus the deltas against the #745 targets.

  python scripts/bench/cpu_mode_ab.py --run --data eval_data/smoke --out-dir ab \
      --base-args "--model small --engine hymt2-cpu" --cpu-args "--engine hymt2-cpu --cpu-mode"
  python scripts/bench/cpu_mode_ab.py ab/base.json ab/cpu.json     # table from two existing reports

--run runs `python -m tlwu_eval --smoke --device cpu --no-noise --latency-jsonl` twice (the #569 eval set, see
docs/testing.md) and writes OUT_DIR/base.json, cpu.json and the two .jsonl latency files.
Targets (#745): p50 drop >= 30%, chrF drop <= 1.0, WER rise <= 2 points. The real numbers are printed either way.

Below the table: the clips whose ASR or MT text differs between A and B. chrF / WER can only move through them; on
the 20-clip smoke set one changed clip moves its pair's chrF by up to ~5 points (#745).

Variants (#745): with --run, every {"name", "args"} entry of --variants FILE (default scripts/bench/cpu_ab_variants.json,
not in the repo: add it on a branch, then dispatch bench-cpu-ab) is run as one more tlwu_eval and gets its own table
against A, e.g. [{"name": "small-beam1-t4", "args": "--engine hymt2-cpu --model small --beam 1 --asr-threads 4"}].
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import statistics
import subprocess
import sys
from pathlib import Path

NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")
VARIANTS_FILE = Path(__file__).with_name("cpu_ab_variants.json")
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


def clip_diffs(rep_a: dict, rep_b: dict) -> list[dict]:
    """Clips (matched by pair + id) whose ASR or MT text differs between two reports: {pair, id, asr, mt} with
    asr / mt = (A text, B text) when that stage differs, else None."""
    def index(rep):
        out = {}
        for lg in rep.get("languages", []):
            for c in lg.get("clips", []):
                key = (lg["pair"], c.get("id"))
                if key in out:  # a repeat would hide the earlier clip's change
                    raise ValueError(f"duplicate clip {key[0]} {key[1]} in a report")
                out[key] = c
        return out
    a, b = index(rep_a), index(rep_b)
    if set(a) != set(b):
        raise ValueError("reports cover different clips: only in A " + (", ".join(f"{p} {i}" for p, i in sorted(set(a) - set(b), key=str)) or "-")
                         + "; only in B " + (", ".join(f"{p} {i}" for p, i in sorted(set(b) - set(a), key=str)) or "-"))
    out = []
    for key in a:
        x, y = a[key], b[key]
        asr = (x.get("asr"), y.get("asr")) if x.get("asr") != y.get("asr") else None
        mt = (x.get("mt"), y.get("mt")) if x.get("mt") != y.get("mt") else None
        if asr or mt:
            out.append({"pair": key[0], "id": key[1], "asr": asr, "mt": mt})
    return out


def diff_report(rep_a: dict, rep_b: dict, limit: int = 10) -> str:
    """Count + the first `limit` clips whose text changed (chrF / WER move only through these)."""
    n = sum(1 for lg in rep_a.get("languages", []) for _ in lg.get("clips", []))
    d = clip_diffs(rep_a, rep_b)
    lines = [f"clips with different ASR text: {sum(1 for x in d if x['asr'])}/{n}; "
             f"different MT text: {sum(1 for x in d if x['mt'])}/{n}"]
    for x in d[:limit]:
        for stage in ("asr", "mt"):
            if x[stage]:
                lines.append(f"- {x['pair']} {x['id']} {stage.upper()}: {x[stage][0]!r} -> {x[stage][1]!r}")
    return "\n".join(lines)


def load_variants(path, required: bool = False) -> list[tuple[str, str]]:
    """[(name, tlwu_eval args)] from a JSON list of {"name", "args"}. A missing default file = no variants; a missing
    file the user named (required=True) is an error, so a requested sweep never silently runs only A and B."""
    p = Path(path)
    if not p.exists():
        if required:
            raise ValueError(f"{p}: variants file not found")
        return []
    data = json.loads(p.read_text("utf-8"))
    if not isinstance(data, list) or not all(isinstance(v, dict) and v.get("name") and "args" in v for v in data):
        raise ValueError(f"{p}: expected a JSON list of {{\"name\", \"args\"}}")
    names = [str(v["name"]) for v in data]
    if any(not NAME_RE.fullmatch(n) for n in names):  # each name is a file stem in --out-dir: no paths, no aliasing
        raise ValueError(f"{p}: variant names must be letters, digits, '-', '_' or '.' (not starting with '.')")
    if len(set(names)) != len(names) or {"base", "cpu"} & {n.lower() for n in names}:  # case-insensitive filesystems
        raise ValueError(f"{p}: variant names must be unique and not 'base' / 'cpu'")
    return [(str(v["name"]), str(v["args"])) for v in data]


def run_eval(extra: str, data: str, out_json: Path, out_jsonl: Path, run=subprocess.run) -> None:
    """Exit 1 = the smoke wrote its report but missed its own PASS/time budget (slow CPU): the A/B table still needs
    those numbers. Any other non-zero exit, or no fresh report, is an error."""
    out_json.unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "tlwu_eval", "--smoke", "--device", "cpu", "--no-noise", "--data", data,
           "--out", str(out_json), "--latency-jsonl", str(out_jsonl), *shlex.split(extra)]
    rc = run(cmd).returncode
    if rc not in (0, 1) or not out_json.exists():
        raise subprocess.CalledProcessError(rc, cmd)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("reports", nargs="*", metavar="BASE.json CPU.json")
    p.add_argument("--run", action="store_true", help="run tlwu_eval twice on CPU first")
    p.add_argument("--data", default="eval_data/smoke")
    p.add_argument("--out-dir", default="ab")
    p.add_argument("--base-args", default="", help="extra tlwu_eval args for the defaults run")
    p.add_argument("--cpu-args", default="", help="extra tlwu_eval args for the CPU-mode run")
    p.add_argument("--variants", default=None,
                   help=f"with --run: JSON list of {{name, args}}, each one more run compared with A (default: {VARIANTS_FILE} if present)")
    a = p.parse_args(argv)
    if a.run and not a.cpu_args.strip():
        p.error("--run needs --cpu-args; without it the CPU-mode run repeats the defaults run")
    d = Path(a.out_dir)
    variants: list[tuple[str, str]] = []
    if a.run:
        try:
            variants = load_variants(a.variants or VARIANTS_FILE, required=a.variants is not None)
        except (OSError, ValueError) as e:
            print(f"cpu_mode_ab: {e}", file=sys.stderr)
            return 2
        d.mkdir(parents=True, exist_ok=True)
        for name, extra in (("base", a.base_args), ("cpu", a.cpu_args), *variants):
            (d / f"{name}.jsonl").unlink(missing_ok=True)
            try:
                run_eval(extra, a.data, d / f"{name}.json", d / f"{name}.jsonl")
            except subprocess.CalledProcessError as e:
                print(f"cpu_mode_ab: {name} run failed (exit {e.returncode})", file=sys.stderr)
                return 2
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
        print()
        print(diff_report(reps[0], reps[1]))
        for name, _args in variants:
            rv = json.loads((d / f"{name}.json").read_text("utf-8"))
            sv = summarize(rv, load_jsonl(d / f"{name}.jsonl") if (d / f"{name}.jsonl").exists() else None)
            check_pairs(sa, sv)
            print(f"\n### variant {name} vs defaults\n")
            print(table(sa, sv))
            print()
            print(diff_report(reps[0], rv))
    except (OSError, ValueError, KeyError) as e:
        print(f"cpu_mode_ab: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
