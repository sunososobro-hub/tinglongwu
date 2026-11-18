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
PRODUCER = "sunososobro-hub"
THROTTLE_ISSUE = 574
DEFAULT_MIX = {"bug": 28, "enhancement": 50, "eval": 11, "documentation": 5, "maint": 6}
TYPES = list(DEFAULT_MIX)
INFO_TARGETS = {"QA": 15, "ideas": 5}
USAGE_RE = re.compile(r"^tlwu-usage:.*\bminutes=(\d+)(?![\d.])", re.M)
LINK_RE = re.compile(r"(?:Closes|Refs) #(\d+)")
HEAD_RE = re.compile(r"^(?:fix|feat)/(\d+)(?:-r\d+)?$")


def linked_issue(head: str, body: str | None) -> int | None:
    """Issue a PR belongs to: the number in its head branch (rules section 2), else the first Closes/Refs in the body."""
    m = HEAD_RE.match(head or "") or LINK_RE.search(body or "")
    return int(m.group(1)) if m else None


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
                try:
                    mix[kv[0]] = int(kv[1])
                except ValueError:  # e.g. "²".isdigit() is True but int() rejects it; absurdly long digit strings too
                    return dict(DEFAULT_MIX), False
            if set(mix) == set(TYPES) and sum(mix.values()) == 100:
                return {t: mix[t] for t in TYPES}, True
    return dict(DEFAULT_MIX), False


def usage_minutes(text: str | None) -> int | None:
    """Minutes from the LAST `tlwu-usage:` line of a body; None when there is none."""
    found = USAGE_RE.findall(text or "")
    if not found or len(found[-1]) > 6:  # bound the field: one absurd value must not stop the hourly report
        return None
    return int(found[-1])


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


def final_usage(body: str | None) -> int | None:
    """Minutes from the LAST non-empty line of a PR body only (rules section 10: it is the last line)."""
    lines = [l for l in (body or "").splitlines() if l.strip()]
    return usage_minutes(lines[-1]) if lines else None


def collect(now: datetime) -> tuple[dict[str, int], int, int, int]:
    """(minutes by type over 48 h incl. QA/ideas, untyped minutes, infra minutes 7 d, total minutes 7 d incl. QA/ideas).

    Every input is bounded by [window start, now]. Usage comments count only from PRODUCER (the owner account the
    routines post as, rules section 14), so an outside commenter cannot skew shares. A failed label lookup raises
    (the job fails and nothing is posted) instead of being counted as "no labels".
    """
    since7 = now - timedelta(days=7)
    since48 = now - timedelta(hours=48)
    by_type: dict[str, int] = {}
    untyped = infra_min = total7 = 0
    labels_cache: dict[int, set[str]] = {}
    incomplete: set[int] = set()
    pulls = _gh_list(f"repos/{REPO}/pulls?state=closed&base=dev&sort=updated&direction=desc&per_page=100")
    for pr in pulls:
        merged = pr.get("merged_at")
        if not merged or not (since7 <= _ts(merged) <= now):
            continue
        mins = final_usage(pr.get("body"))
        if not mins:
            continue
        n = linked_issue(pr["head"]["ref"], pr.get("body"))
        labels: set[str] = set()
        if n is not None:
            if n not in labels_cache:
                try:
                    labels_cache[n] = {l["name"] for l in _gh(f"repos/{REPO}/issues/{n}")["labels"]}
                except subprocess.CalledProcessError as e:
                    if "404" not in (e.stderr or ""):
                        raise  # transient failure: fail the run, publish nothing wrong
                    labels_cache[n] = set()  # linked issue deleted: keep the minutes, no classification
                    incomplete.add(n)
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

    def add_comment(kind: str, c: dict) -> None:
        nonlocal total7
        m = usage_minutes(c.get("body"))
        if not m or (c.get("user") or {}).get("login") != PRODUCER or "Ledger (48 h" in (c.get("body") or ""):
            return
        t = _ts(c["created_at"])
        if not (since7 <= t <= now):
            return
        total7 += m
        if t >= since48:
            by_type[kind] = by_type.get(kind, 0) + m

    for c in _gh_list(f"repos/{REPO}/issues/{LOG_ISSUE}/comments?since={since7:%Y-%m-%dT%H:%M:%SZ}&per_page=100"):
        add_comment("QA", c)
    for i in _gh_list(f"repos/{REPO}/issues?state=all&since={since7:%Y-%m-%dT%H:%M:%SZ}&per_page=100"):
        if i["title"].startswith("[idea]") and "pull_request" not in i:
            for c in _gh_list(f"repos/{REPO}/issues/{i['number']}/comments?per_page=100"):
                add_comment("ideas", c)
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
    subprocess.run(["gh", "api", f"repos/{REPO}/issues/{LOG_ISSUE}/comments", "-f", f"body={text}"], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
