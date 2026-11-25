"""#1902 (C3 of #1875): weekly collision table for PRs merged to dev in the last 7 days.

Per file: PRs touching / overlapping pairs (both PRs open at the same time) / dirty requeues (top 10).
Plus: touch-up pushes after the PR opened, split by cause (review, red test, conflict); redo count (`fix/N-rK`
heads); dev-red count (failed `test` runs on dev pushes); `test` p50 minutes on the merged PR heads.

Usage:
  python scripts/collision_ledger.py [--post]          live data, GitHub REST only (needs `gh` with GH_TOKEN)
  python scripts/collision_ledger.py --fixture F.json  offline: F holds the already collected input (see FIXTURE)
--post (off by default) adds the table as a comment on the `[health] pipeline check log` issue (#61).

FIXTURE: {"now": iso, "dev_red": int, "prs": [{"number", "head", "created_at", "merged_at", "files": [..],
  "dirty_requeues": int, "test_minutes": float|null, "pushes": ["review"|"red test"|"conflict", ..]}]}
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import combinations

REPO = "sunososobro-hub/tinglongwu"
LOG_ISSUE = 61
DAYS = 7
TOP = 10
CAUSES = ("review", "red test", "conflict")
REDO = re.compile(r"^fix/\d+-r\d+$")
FIX_HEAD = re.compile(r"^fix/(\d+)")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def overlaps(a: dict, b: dict) -> bool:
    """Both PRs were open at the same time (created .. merged windows intersect)."""
    return _ts(a["created_at"]) < _ts(b["merged_at"]) and _ts(b["created_at"]) < _ts(a["merged_at"])


def file_rows(prs: list[dict]) -> list[dict]:
    """One row per file touched by any merged PR; sorted by pairs, PRs, requeues, name; top 10."""
    by_file: dict[str, list[dict]] = {}
    for pr in prs:
        for f in sorted(set(pr.get("files", []))):
            by_file.setdefault(f, []).append(pr)
    rows = []
    for f, group in by_file.items():
        pairs = sum(1 for a, b in combinations(group, 2) if overlaps(a, b))
        rows.append({"file": f, "prs": len(group), "pairs": pairs,
                     "dirty": sum(int(p.get("dirty_requeues", 0)) for p in group)})
    rows.sort(key=lambda r: (-r["pairs"], -r["prs"], -r["dirty"], r["file"]))
    return rows[:TOP]


def push_causes(prs: list[dict]) -> dict[str, int]:
    c = Counter(cause for p in prs for cause in p.get("pushes", []))
    return {k: c.get(k, 0) for k in CAUSES}


def redo_count(prs: list[dict]) -> int:
    return sum(1 for p in prs if REDO.match(p.get("head", "")))


def test_p50(prs: list[dict]) -> float | None:
    vals = [float(p["test_minutes"]) for p in prs if p.get("test_minutes") is not None]
    return round(statistics.median(vals), 1) if vals else None


def render(data: dict) -> str:
    prs = data["prs"]
    out = [f"## collision ledger ({len(prs)} PRs merged to dev in the last {DAYS} days)", "",
           "| file | PRs touching | overlapping pairs | dirty requeues |", "|---|---|---|---|"]
    out += [f"| {r['file']} | {r['prs']} | {r['pairs']} | {r['dirty']} |" for r in file_rows(prs)]
    pc = push_causes(prs)
    p50 = test_p50(prs)
    out += ["", "| metric | value |", "|---|---|"]
    out += [f"| touch-up pushes: {k} | {pc[k]} |" for k in CAUSES]
    out += [f"| touch-up pushes: total | {sum(pc.values())} |", f"| redo (fix/N-rK heads) | {redo_count(prs)} |",
            f"| dev-red (failed test runs on dev) | {int(data.get('dev_red', 0))} |",
            f"| test p50 minutes | {'n/a' if p50 is None else p50} |"]
    return "\n".join(out)


# ---- live collection (REST only) -------------------------------------------------------------------------

def _gh(path: str, *args: str):
    r = subprocess.run(["gh", "api", path, *args], capture_output=True, text=True, check=True)
    return json.loads(r.stdout or "null")


def _gh_list(path: str, pages: int = 5) -> list:
    sep = "&" if "?" in path else "?"
    items: list = []
    for n in range(1, pages + 1):
        chunk = _gh(f"{path}{sep}per_page=100&page={n}")
        items += chunk
        if len(chunk) < 100:
            break
    return items


def classify_push(message: str, red_before: bool) -> str:
    if message.lower().startswith("merge"):
        return "conflict"
    return "red test" if red_before else "review"


def _test_run(sha: str) -> dict | None:
    runs = _gh(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100").get("check_runs", [])
    tests = [r for r in runs if r["name"] == "test" and r.get("completed_at")]
    return max(tests, key=lambda r: r["completed_at"]) if tests else None


def collect(now: datetime) -> dict:
    since = now - timedelta(days=DAYS)
    merged = [p for p in _gh_list(f"repos/{REPO}/pulls?state=closed&base=dev&sort=updated&direction=desc")
              if p.get("merged_at") and _ts(p["merged_at"]) >= since]
    prs = []
    for p in merged:
        n = p["number"]
        files = [f["filename"] for f in _gh_list(f"repos/{REPO}/pulls/{n}/files")]
        commits = _gh_list(f"repos/{REPO}/pulls/{n}/commits")
        opened = _ts(p["created_at"])
        pushes, red = [], False
        for c in commits:
            if _ts(c["commit"]["committer"]["date"]) <= opened:
                continue
            pushes.append(classify_push(c["commit"]["message"], red))
            run = _test_run(c["sha"])
            red = bool(run and run["conclusion"] == "failure")
        m = FIX_HEAD.match(p["head"]["ref"])
        dirty = 0
        if m:
            cs = _gh_list(f"repos/{REPO}/issues/{m.group(1)}/comments")
            dirty = sum(1 for c in cs if c["body"].startswith("requeue: dirty"))
        last = _test_run(p["head"]["sha"])
        minutes = (_ts(last["completed_at"]) - _ts(last["started_at"])).total_seconds() / 60 if last else None
        prs.append({"number": n, "head": p["head"]["ref"], "created_at": p["created_at"],
                    "merged_at": p["merged_at"], "files": files, "dirty_requeues": dirty,
                    "test_minutes": minutes, "pushes": pushes})
    dev_red = 0
    for run in _gh(f"repos/{REPO}/actions/workflows/ci.yml/runs?branch=dev&event=push&per_page=100")["workflow_runs"]:
        if _ts(run["created_at"]) >= since and run["conclusion"] == "failure":
            dev_red += 1
    return {"now": now.isoformat(), "dev_red": dev_red, "prs": prs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fixture", nargs="+", help="offline input JSON file(s); their PR lists are merged")
    ap.add_argument("--post", action="store_true", help="comment the table on #61 (off by default)")
    args = ap.parse_args(argv)
    if args.fixture:
        parts = [json.load(open(f, encoding="utf-8")) for f in args.fixture]
        data = {"dev_red": sum(int(d.get("dev_red", 0)) for d in parts), "prs": [p for d in parts for p in d["prs"]]}
    else:
        data = collect(datetime.now(UTC))
    table = render(data)
    print(table)
    if args.post and not args.fixture:
        subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/issues/{LOG_ISSUE}/comments", "-f",
                        f"body={table}"], check=True, capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
