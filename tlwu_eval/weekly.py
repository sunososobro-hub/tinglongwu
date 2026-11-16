"""#643 (#619 part 4): weekly per-language verdict from the eval-langs.yml pair jobs.

  python -m tlwu_eval.weekly collect ARTIFACTS_DIR --out langs.json [--date YYYY-MM-DD] [--run-url URL]
  python -m tlwu_eval.weekly alert langs.json --out alert.md     # prints fail=true|false (also to $GITHUB_OUTPUT)

`collect` reads the `eval-langs-<src>-<tgt>/r.json` artifact of every pair the matrix ran (eval/languages.json, pins
present, not blocked) and gates it against that pair's eval/baseline.json "matrix" entry with the same rule as the
PR job (tlwu_eval/gate.py: chrF - margin, MT median budget). A pair whose job left no report (crashed, timed out) is a
FAIL, so a broken runner never reads as a quiet week. The result goes to `langs/YYYY-MM-DD.json` on the
`eval-results` branch; scripts/stats_dashboard.py renders the latest two as the per-language table."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from tlwu_eval import gate, langmatrix


def pair_verdict(report: dict | None, pair: str, baseline: dict) -> dict:
    """{pair, clips, asr_error_rate, chrf, mt_median_s, verdict, why} for one pair."""
    row = {"pair": pair, "clips": 0, "asr_error_rate": None, "chrf": None, "mt_median_s": None,
           "verdict": "FAIL", "why": ""}
    if report is None:
        row["why"] = "no report (the pair job failed before writing r.json)"
        return row
    lg = next((x for x in report.get("languages", []) if isinstance(x, dict) and x.get("pair") == pair), None) \
        if isinstance(report, dict) else None
    if lg is not None:
        row["clips"] = len(lg.get("clips") or [])
        for k_out, k_in in (("asr_error_rate", "error_rate"), ("chrf", "chrf"), ("mt_median_s", "mt_median_s")):
            v = lg.get(k_in)
            row[k_out] = v if gate._num(v) else None
    ok, line = langmatrix.summary_line(report, pair)
    if not ok:
        row["why"] = line.split(" - ", 1)[-1] if " - " in line else line
        return row
    try:
        g = gate.compare(report, langmatrix.pair_baseline(pair, baseline))[0]
    except gate.GateError as e:
        row["why"] = str(e)
        return row
    row["verdict"] = "PASS" if g["result"] == "PASS" else "FAIL"
    row["why"] = g["why"] if row["verdict"] == "FAIL" else ""
    return row


def collect(art_dir, config=langmatrix.CONFIG, baseline_path=langmatrix.BASELINE, date=None, run_url="") -> dict:
    entries = langmatrix.load_config(config)
    jobs, _ = langmatrix.matrix(entries, Path(config).resolve().parents[1])
    baseline = gate.validate_baseline(gate.load_json(baseline_path, "baseline"))
    rows = []
    for j in jobs:
        p = Path(art_dir) / f"eval-langs-{j['src']}-{j['tgt']}" / "r.json"
        try:
            report = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            report = None
        rows.append(pair_verdict(report, j["pair"], baseline))
    return {"date": date or datetime.date.today().isoformat(), "run_url": run_url, "languages": rows}


def _f(v, spec):
    return "n/a" if v is None else format(v, spec)


def alert_markdown(doc: dict) -> str:
    """One #61 comment naming every failing pair and its scores; '' when nothing failed."""
    bad = [r for r in doc.get("languages", []) if r.get("verdict") != "PASS"]
    if not bad:
        return ""
    lines = [f"eval-langs weekly {doc.get('date', '')}: FAIL for " + ", ".join(r["pair"] for r in bad) + ".", "",
             "| pair | ASR error % | chrF | MT median s | why |", "|---|---|---|---|---|"]
    for r in bad:
        er = None if r.get("asr_error_rate") is None else r["asr_error_rate"] * 100
        lines.append(f"| {r['pair']} | {_f(er, '.2f')} | {_f(r.get('chrf'), '.2f')} | "
                     f"{_f(r.get('mt_median_s'), '.3f')} | {r.get('why') or ''} |")
    if doc.get("run_url"):
        lines += ["", f"Run: {doc['run_url']}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m tlwu_eval.weekly", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("artifacts")
    c.add_argument("--out", required=True)
    c.add_argument("--date", default=None)
    c.add_argument("--run-url", default="")
    c.add_argument("--config", default=str(langmatrix.CONFIG))
    c.add_argument("--baseline", default=str(langmatrix.BASELINE))
    a_ = sub.add_parser("alert")
    a_.add_argument("doc")
    a_.add_argument("--out", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "collect":
            doc = collect(a.artifacts, a.config, a.baseline, a.date, a.run_url)
            Path(a.out).write_text(json.dumps(doc, indent=2) + "\n", "utf-8")
            for r in doc["languages"]:
                print(f"{r['pair']}: {r['verdict']}" + (f" ({r['why']})" if r["why"] else ""))
            return 0
        text = alert_markdown(gate.load_json(a.doc, "weekly result"))
    except (langmatrix.ConfigError, gate.GateError) as e:
        print(f"weekly: {e}", file=sys.stderr)
        return 2
    if text:
        Path(a.out).write_text(text, "utf-8")
    flag = "true" if text else "false"
    print(f"fail={flag}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"fail={flag}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
