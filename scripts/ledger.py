"""#1469: hourly ledger (rules section 10) + 7-day infra line (section 13), posted on #61 by a scheduled Action.

Usage: python scripts/ledger.py [--dry-run]   (needs `gh` with GH_TOKEN; --dry-run prints instead of posting)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, UTC

REPO = "sunososobro-hub/tinglongwu"
LOG_ISSUE = 61
THROTTLE_ISSUE = 574
DEFAULT_MIX = {"bug": 28, "enhancement": 50, "eval": 11, "documentation": 5, "maint": 6}
TYPES = list(DEFAULT_MIX)
INFO_TARGETS = {"QA": 15, "ideas": 5}
USAGE_RE = re.compile(r"^tlwu-usage:.*\bminutes=(\d+)", re.M)
LINK_RE = re.compile(r"(?:Closes|Refs) #(\d+)")


def parse_mix(body: str | None) -> tuple[dict[str, int], bool]:
    """Targets from line 3 `mix: bug 28, ...` of #574. (mix, valid); invalid/missing -> (default, False)."""
    lines = (body or "").splitlines()
    if len(lines) >= 3:
        m = re.fullmatch(r"mix:\s*(.+)", lines[2].strip())
        if m:
            mix: dict[str, int] = {}
            for part in m.group(1).split(","):
                kv = part.split()
                if len(kv) != 2 or not kv[1].isdigit() or kv[0] in mix:
                    return dict(DEFAULT_MIX), False
                mix[kv[0]] = int(kv[1])
            if set(mix) == set(TYPES) and sum(mix.values()) == 100:
                return {t: mix[t] for t in TYPES}, True
    return dict(DEFAULT_MIX), False


def usage_minutes(text: str | None) -> int | None:
    """Minutes from the LAST `tlwu-usage:` line of a body; None when there is none."""
    found = USAGE_RE.findall(text or "")
    return int(found[-1]) if found else None


def build_rows(minutes: dict[str, int], mix: dict[str, int]) -> list[dict]:
    """Rows type|minutes|share|target|deficit; share over the total of all rows, 0 when the total is 0."""
    targets = {**mix, **INFO_TARGETS}
    total = sum(minutes.get(t, 0) for t in targets)
    rows = []
    for t, target in targets.items():
        mins = minutes.get(t, 0)
        share = round(100 * mins / total, 1) if total else 0.0
        rows.append({"type": t, "minutes": mins, "share": share, "target": target,
                     "deficit": round(target - share, 1)})
    return rows


def infra_line(infra_min: int, total_min: int) -> str:
    share = round(100 * infra_min / total_min, 1) if total_min else 0.0
    return f"infra: {infra_min} / {total_min} = {share}% (cap 10%)"


def render(rows: list[dict], infra: str, total: int, untyped: int, mix_ok: bool, now: datetime) -> str:
    out = [f"Ledger (48 h to {now:%H:%M}Z, PRs merged to dev + QA/idea usage comments, total {total} min; "
           f"untyped PRs excluded: {untyped} min)"]
    if not mix_ok:
        out.append("mix invalid, default used")
    out += ["", "| type | minutes | share | target | deficit |", "|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['type']} | {r['minutes']} | {r['share']}% | {r['target']}% | {r['deficit']:+.1f} |")
    out += ["", infra, "", "_posted by the ledger Action (#1469)_"]
    return "\n".join(out)


def _gh(path: str, *args: str) -> object:
    out = subprocess.run(["gh", "api", path, *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _gh_list(path: str) -> list:
    pages = _gh(path, "--paginate", "--slurp") or []
    return [i for p in pages for i in (p if isinstance(p, list) else [p])]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def collect(now: datetime) -> tuple[dict[str, int], int, int, int]:
    """(minutes by type over 48 h incl. QA/ideas, untyped minutes, infra minutes 7 d, total minutes 7 d)."""
    since7 = now - timedelta(days=7)
    since48 = now - timedelta(hours=48)
    by_type: dict[str, int] = {}
    untyped = infra_min = total7 = 0
    labels_cache: dict[int, set[str]] = {}
    pulls = _gh_list(f"repos/{REPO}/pulls?state=closed&base=dev&sort=updated&direction=desc&per_page=100")
    for pr in pulls:
        merged = pr.get("merged_at")
        if not merged or _ts(merged) < since7:
            continue
        mins = usage_minutes(pr.get("body"))
        if not mins:
            continue
        link = LINK_RE.search(pr.get("body") or "")
        labels: set[str] = set()
        if link:
            n = int(link.group(1))
            if n not in labels_cache:
                try:
                    labels_cache[n] = {l["name"] for l in _gh(f"repos/{REPO}/issues/{n}")["labels"]}
                except subprocess.CalledProcessError:
                    labels_cache[n] = set()
            labels = labels_cache[n]
        total7 += mins
        if "infra" in labels:
            infra_min += mins
        if _ts(merged) >= since48:
            typ = next((t for t in TYPES if t in labels), None)
            if typ:
                by_type[typ] = by_type.get(typ, 0) + mins
            else:
                untyped += mins
    for c in _gh_list(f"repos/{REPO}/issues/{LOG_ISSUE}/comments?since={since48:%Y-%m-%dT%H:%M:%SZ}&per_page=100"):
        m = usage_minutes(c.get("body"))
        if m and _ts(c["created_at"]) >= since48 and "Ledger (48 h" not in (c.get("body") or ""):
            by_type["QA"] = by_type.get("QA", 0) + m
    for i in _gh_list(f"repos/{REPO}/issues?state=all&since={since48:%Y-%m-%dT%H:%M:%SZ}&per_page=100"):
        if i["title"].startswith("[idea]") and "pull_request" not in i:
            for c in _gh_list(f"repos/{REPO}/issues/{i['number']}/comments?per_page=100"):
                m = usage_minutes(c.get("body"))
                if m and _ts(c["created_at"]) >= since48:
                    by_type["ideas"] = by_type.get("ideas", 0) + m
    return by_type, untyped, infra_min, total7


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now = datetime.now(UTC)
    mix, ok = parse_mix(_gh(f"repos/{REPO}/issues/{THROTTLE_ISSUE}")["body"])
    by_type, untyped, infra_min, total7 = collect(now)
    rows = build_rows(by_type, mix)
    text = render(rows, infra_line(infra_min, total7), sum(r["minutes"] for r in rows), untyped, ok, now)
    if args.dry_run:
        print(text)
        return 0
    subprocess.run(["gh", "api", f"repos/{REPO}/issues/{LOG_ISSUE}/comments", "-f", f"body={text}"],
                   check=True, capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
