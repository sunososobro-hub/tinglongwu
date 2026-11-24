"""#1469: hourly ledger (rules section 10) + 7-day infra line (section 13), posted on #61 by a scheduled Action.

#1581: review-fix minutes after a PR opens (`tlwu-usage: ... minutes=N phase=fix` comments on the PR) are added to
the PR's type and shown on their own line, so the cost of review rounds is visible.

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
DEFAULT_MIX = {"bug": 28, "enhancement": 40, "infra": 10, "eval": 11, "documentation": 5, "maint": 6}
TYPES = list(DEFAULT_MIX)
INFO_TARGETS = {"QA": 10, "ideas": 5}
OWNER_LABEL = "src:owner"  # owner 2026-11-20: owner-requested work is exempt from the mix and the infra cap
OWNER_KEY = "owner"  # by_type key for exempt minutes; not a target, so build_rows never counts it
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


def pick_type(labels: set[str]) -> str | None:
    """Mix bucket of an issue: `infra` wins (infra issues also carry a type label, section 13), else the first TYPES hit."""
    if "infra" in labels:
        return "infra"
    return next((t for t in TYPES if t in labels), None)


def owner_line(owner48: int) -> str:
    return f"owner-requested (src:owner, exempt from mix): {owner48} min (48 h)"


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
        if OWNER_LABEL in labels:
            if _ts(merged) >= since48:
                by_type[OWNER_KEY] = by_type.get(OWNER_KEY, 0) + mins
            continue
        total7 += mins
        if "infra" in labels:
            infra_min += mins
        if _ts(merged) >= since48:
            typ = pick_type(labels)
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


FIX_RE = re.compile(r"^tlwu-usage:.*\bphase=fix\b.*$", re.M)
PR_URL_RE = re.compile(r"/pull/(\d+)$")


def fix_minutes(body: str | None) -> int | None:
    """#1581: minutes of the LAST `tlwu-usage: ... phase=fix` line of a PR comment; None when there is none."""
    found = FIX_RE.findall(body or "")
    return usage_minutes(found[-1]) if found else None


def collect_fix(now: datetime, pr_issue=None) -> tuple[dict[str, int], int, int, int, int]:
    """#1581: review-fix minutes from producer `phase=fix` usage comments on PRs (any state), by comment time.

    Returns (minutes by type over 48 h, untyped 48 h, infra minutes 7 d, total minutes 7 d, fix minutes 48 h).
    One repo-wide comments listing per run (no per-PR calls), then one lookup per distinct PR and issue.
    """
    since7, since48 = now - timedelta(days=7), now - timedelta(hours=48)
    by_type: dict[str, int] = {}
    untyped = infra_min = total7 = fix48 = 0
    issue_of: dict[int, int | None] = {}
    labels_cache: dict[int, set[str]] = {}

    def issue_for(num: int) -> int | None:
        if num not in issue_of:
            pr = _gh(f"repos/{REPO}/pulls/{num}")
            issue_of[num] = linked_issue(pr["head"]["ref"], pr.get("body"))
        return issue_of[num]

    pr_issue = pr_issue or issue_for
    for c in _gh_list(f"repos/{REPO}/issues/comments?since={since7:%Y-%m-%dT%H:%M:%SZ}&per_page=100"):
        if (c.get("user") or {}).get("login") != PRODUCER:
            continue
        m = PR_URL_RE.search(c.get("html_url", "").split("#")[0])
        mins = fix_minutes(c.get("body"))
        t = _ts(c["created_at"])
        if not m or not mins or not (since7 <= t <= now):
            continue
        n = pr_issue(int(m.group(1)))
        labels: set[str] = set()
        if n is not None:
            if n not in labels_cache:
                try:
                    labels_cache[n] = {l["name"] for l in _gh(f"repos/{REPO}/issues/{n}")["labels"]}
                except subprocess.CalledProcessError as e:
                    if "404" not in (e.stderr or ""):
                        raise
                    labels_cache[n] = set()
            labels = labels_cache[n]
        if OWNER_LABEL in labels:
            if t >= since48:
                by_type[OWNER_KEY] = by_type.get(OWNER_KEY, 0) + mins
            continue
        total7 += mins
        if "infra" in labels:
            infra_min += mins
        if t >= since48:
            fix48 += mins
            typ = pick_type(labels)
            if typ:
                by_type[typ] = by_type.get(typ, 0) + mins
            else:
                untyped += mins
    return by_type, untyped, infra_min, total7, fix48


def fix_line(fix48: int, total48: int) -> str:
    share = round(100 * fix48 / total48, 1) if total48 else 0.0
    return f"open->merge fix minutes (48 h): {fix48} ({share}% of total)"


# --- #1676: per-model quality metrics over the last 14 days (owner a1: decide model pairings by data) -------------
MODEL_DAYS = 14
INCIDENT_DAYS = 7
MODEL_RE = re.compile(r"^tlwu-usage:.*\bmodel=([\w.\-]+)", re.M)
REVIEW_FAIL_RE = re.compile(r"AI review: FAIL|Opus review: CHANGES")
REVERT_RE = re.compile(r"\brevert", re.I)


def pr_model(body: str | None) -> str:
    """Builder model from the LAST non-empty line of a PR body (`tlwu-usage: model=<m> ...`); `unknown` when absent."""
    lines = [l for l in (body or "").splitlines() if l.strip()]
    m = MODEL_RE.search(lines[-1]) if lines else None
    return m.group(1) if m else "unknown"


def has_review_fail(comment_bodies: list[str]) -> bool:
    return any(REVIEW_FAIL_RE.search(b or "") for b in comment_bodies)


def red_rounds(test_conclusions: dict[str, list[str]]) -> int:
    """Head commits (sha -> conclusions of their `test` check runs) whose `test` concluded failure."""
    return sum(1 for runs in test_conclusions.values() if "failure" in runs)


def is_incident(pr: int, issue: int | None, merged_at: datetime, issues: list[dict], later_prs: list[dict]) -> bool:
    """Reverted on dev (a later dev PR titled/bodied `revert` that names this PR), or a `bug` issue other than the
    PR's own, opened within 7 days after the merge, whose body refers to the PR or to its linked issue."""
    refs = {f"#{pr}"} | ({f"#{issue}"} if issue is not None else set())
    for o in later_prs:
        text = f"{o.get('title') or ''}\n{o.get('body') or ''}"
        if o.get("number") != pr and REVERT_RE.search(o.get("title") or "") and any(
                re.search(re.escape(r) + r"(?!\d)", text) for r in refs):
            return True
    end = merged_at + timedelta(days=INCIDENT_DAYS)
    for i in issues:
        if i.get("number") in (issue, pr) or "bug" not in i.get("labels", []):
            continue
        if merged_at <= i["created_at"] <= end and any(re.search(re.escape(r) + r"(?!\d)", i.get("body") or "") for r in refs):
            return True
    return False


def model_rows(prs: list[dict]) -> list[dict]:
    """prs: dicts with model, review_fail (bool), red (int), incident (bool). One row per model, most PRs first."""
    by: dict[str, list[dict]] = {}
    for p in prs:
        by.setdefault(p["model"], []).append(p)
    rows = []
    for model, ps in by.items():
        n = len(ps)
        rows.append({"model": model, "prs": n,
                     "fail_rate": round(100 * sum(1 for p in ps if p["review_fail"]) / n, 1),
                     "red_avg": round(sum(p["red"] for p in ps) / n, 2),
                     "incidents": sum(1 for p in ps if p["incident"])})
    return sorted(rows, key=lambda r: (-r["prs"], r["model"]))


def render_models(rows: list[dict]) -> str:
    out = [f"Quality by builder model ({MODEL_DAYS} d, PRs merged to dev)", "",
           "| model | PRs merged | review-fail rate | avg CI red rounds | post-merge incidents |", "|---|---|---|---|---|"]
    out += [f"| {r['model']} | {r['prs']} | {r['fail_rate']}% | {r['red_avg']} | {r['incidents']} |" for r in rows]
    if not rows:
        out.append("| (no merged PRs) | 0 | 0.0% | 0.0 | 0 |")
    return "\n".join(out)


def collect_models(now: datetime) -> list[dict]:
    """Per-PR facts for PRs merged to dev in the last 14 days, one gh call per PR for comments and per head commit."""
    since = now - timedelta(days=MODEL_DAYS)
    prs = [p for p in _gh_list(f"repos/{REPO}/pulls?state=closed&base=dev&sort=updated&direction=desc&per_page=100")
           if p.get("merged_at") and since <= _ts(p["merged_at"]) <= now]
    issues = [{"number": i["number"], "created_at": _ts(i["created_at"]), "body": i.get("body"),
               "labels": [l["name"] for l in i["labels"]]}
              for i in _gh_list(f"repos/{REPO}/issues?state=all&labels=bug&since={since:%Y-%m-%dT%H:%M:%SZ}&per_page=100")
              if "pull_request" not in i and _ts(i["created_at"]) >= since]
    out = []
    for p in prs:
        n = p["number"]
        merged = _ts(p["merged_at"])
        comments = [c.get("body") or "" for c in _gh_list(f"repos/{REPO}/issues/{n}/comments?per_page=100")]
        conclusions: dict[str, list[str]] = {}
        for c in _gh_list(f"repos/{REPO}/pulls/{n}/commits?per_page=100"):
            runs = _gh(f"repos/{REPO}/commits/{c['sha']}/check-runs?check_name=test&per_page=100") or {}
            conclusions[c["sha"]] = [r.get("conclusion") for r in runs.get("check_runs", [])]
        later = [{"number": o["number"], "title": o.get("title"), "body": o.get("body")}
                 for o in prs if o.get("merged_at") and _ts(o["merged_at"]) > merged]
        out.append({"model": pr_model(p.get("body")), "review_fail": has_review_fail(comments),
                    "red": red_rounds(conclusions),
                    "incident": is_incident(n, linked_issue(p["head"]["ref"], p.get("body")), merged, issues, later)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now = datetime.now(UTC)
    mix, ok = parse_mix(_gh(f"repos/{REPO}/issues/{THROTTLE_ISSUE}")["body"])
    by_type, untyped, infra_min, total7 = collect(now)
    f_type, f_untyped, f_infra, f_total7, fix48 = collect_fix(now)
    for t, m in f_type.items():
        by_type[t] = by_type.get(t, 0) + m
    untyped, infra_min, total7 = untyped + f_untyped, infra_min + f_infra, total7 + f_total7
    rows = build_rows(by_type, mix)
    total = sum(r["minutes"] for r in rows)
    infra = infra_line(infra_min, total7) + "\n" + fix_line(fix48, total + untyped)
    infra += "\n" + owner_line(by_type.get(OWNER_KEY, 0))
    text = render(rows, infra, total, untyped, ok, now)
    text = text.replace("\n\n_posted by", "\n\n" + render_models(model_rows(collect_models(now))) + "\n\n_posted by", 1)
    if args.dry_run:
        print(text)
        return 0
    subprocess.run(["gh", "api", f"repos/{REPO}/issues/{LOG_ISSUE}/comments", "-f", f"body={text}"], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
