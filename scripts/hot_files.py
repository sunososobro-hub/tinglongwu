"""#1917 (A8 of #1875): which files do the most PRs share, and where do merge-from-dev commits collide?

For the PRs into dev merged in the last N days plus every open dev PR, per file:
- PRs: how many of those PRs change the file;
- conflict merges: how many merge commits inside those PRs (a merge of dev into the PR branch) leave the file
  different from BOTH parents, i.e. both sides had changed it (git's own conflict candidates; a clean
  auto-merge of two hunks counts too, since REST cannot tell the two apart).
Prints the top 15 by PRs and the top 15 by conflict merges, so the files to split (A8) are picked from data.

Usage:
  python scripts/hot_files.py [--days 14] [--top 15] [--post ISSUE]   live data, GitHub REST only (`gh`)
  python scripts/hot_files.py --fixture F.json                         offline: F holds the collected input
--post ISSUE (off by default) adds the tables as one comment on that issue, first line `[hot files Nd]`.

FIXTURE: {"days": int, "prs": [{"number": int, "files": [..], "conflict_files": [[..], ..]}]}
  conflict_files has one list per merge commit of that PR: the files that differ from both parents.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

REPO = "sunososobro-hub/tinglongwu"
DAYS = 14
TOP = 15


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def counts(prs: list[dict]) -> tuple[Counter, Counter]:
    """(PRs touching each file, merge commits whose result differs from both parents per file)."""
    touched: Counter = Counter()
    merged: Counter = Counter()
    for pr in prs:
        touched.update(set(pr.get("files", [])))
        for files in pr.get("conflict_files", []):
            merged.update(set(files))
    return touched, merged


def top(counter: Counter, n: int) -> list[tuple[str, int]]:
    """Highest count first, ties by file name; zero counts never listed."""
    return sorted(((f, c) for f, c in counter.items() if c > 0), key=lambda fc: (-fc[1], fc[0]))[:n]


def render(data: dict, n: int = TOP) -> str:
    prs = data["prs"]
    touched, merged = counts(prs)
    days = int(data.get("days", DAYS))
    n_merges = sum(len(p.get("conflict_files", [])) for p in prs)
    out = [f"## hot files ({len(prs)} dev PRs: merged in the last {days} days + open; "
           f"{n_merges} merge-from-dev commits)", "",
           f"### top {n} by PRs touching", "", "| # | file | PRs | conflict merges |", "|---|---|---|---|"]
    out += [f"| {i} | {f} | {c} | {merged.get(f, 0)} |" for i, (f, c) in enumerate(top(touched, n), 1)]
    out += ["", f"### top {n} by conflict merges (file changed on both sides of a merge from dev)", "",
            "| # | file | conflict merges | PRs |", "|---|---|---|---|"]
    rows = top(merged, n)
    out += [f"| {i} | {f} | {c} | {touched.get(f, 0)} |" for i, (f, c) in enumerate(rows, 1)]
    if not rows:
        out.append("| - | none | 0 | 0 |")
    return "\n".join(out)


# ---- live collection (REST only) -------------------------------------------------------------------------

def _gh(path: str):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True)
    return json.loads(r.stdout or "null")


def _gh_list(path: str, key: str | None = None, max_pages: int = 100) -> list:
    """All pages of a REST list; reaching max_pages fails loudly instead of returning a partial list."""
    sep = "&" if "?" in path else "?"
    items: list = []
    for page in range(1, max_pages + 1):
        data = _gh(f"{path}{sep}per_page=100&page={page}")
        chunk = data[key] if key else data
        items += chunk
        if len(chunk) < 100:
            return items
    raise RuntimeError(f"{path}: more than {max_pages} pages")


def both_sides(first_parent_files: list[str], second_parent_files: list[str]) -> list[str]:
    """Files a merge commit changed relative to each of its two parents = changed on both sides."""
    return sorted(set(first_parent_files) & set(second_parent_files))


def merge_conflict_files(sha: str, parents: list[str]) -> list[str]:
    vs_first = [f["filename"] for f in _gh_list(f"repos/{REPO}/commits/{sha}", key="files")]
    if not vs_first:
        return []
    vs_second = [f["filename"] for f in _gh(f"repos/{REPO}/compare/{parents[1]}...{sha}").get("files", [])]
    return both_sides(vs_first, vs_second)


def _pr_entry(p: dict) -> dict:
    n = p["number"]
    files = [f["filename"] for f in _gh_list(f"repos/{REPO}/pulls/{n}/files")]
    conflicts = [merge_conflict_files(c["sha"], [x["sha"] for x in c["parents"]])
                 for c in _gh_list(f"repos/{REPO}/pulls/{n}/commits") if len(c.get("parents", [])) == 2]
    print(f"PR #{n}: {len(files)} files, {len(conflicts)} merges", file=sys.stderr)
    return {"number": n, "files": files, "conflict_files": conflicts}


def collect(now: datetime, days: int) -> dict:
    since = now - timedelta(days=days)
    pulls = []
    for page in range(1, 101):  # newest update first; a PR merged since `since` was also updated since then
        chunk = _gh(f"repos/{REPO}/pulls?state=closed&base=dev&sort=updated&direction=desc&per_page=100&page={page}")
        pulls += [p for p in chunk if p.get("merged_at") and _ts(p["merged_at"]) >= since]
        if len(chunk) < 100 or _ts(chunk[-1]["updated_at"]) < since:
            break
    pulls += _gh_list(f"repos/{REPO}/pulls?state=open&base=dev")
    with ThreadPoolExecutor(max_workers=8) as pool:  # I/O bound: one `gh` process per call
        prs = list(pool.map(_pr_entry, pulls))
    return {"days": days, "prs": prs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--top", type=int, default=TOP)
    ap.add_argument("--fixture", help="offline input JSON file")
    ap.add_argument("--post", type=int, metavar="ISSUE", help="comment the tables on this issue (off by default)")
    args = ap.parse_args(argv)
    if args.fixture:
        with open(args.fixture, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = collect(datetime.now(UTC), args.days)
    text = render(data, args.top)
    print(text)
    if args.post and not args.fixture:
        subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/issues/{args.post}/comments", "-f",
                        f"body=[hot files {data['days']}d]\n{text}"], check=True, capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
