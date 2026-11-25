"""#1469: hourly ledger (rules section 10) + 7-day infra line (section 13), posted on #61 by a scheduled Action.

#1581: review-fix minutes after a PR opens (`tlwu-usage: ... minutes=N phase=fix` comments on the PR) are added to
the PR's type and shown on their own line, so the cost of review rounds is visible.

#1919: one `ready=<n> startable=<n> blocked_by_dep=<n> workers_24h=<n> target=<n>` line (ticket supply, read by S-split).

#1915: the ledger weighs work by `tool_calls=N` (each call resends the context, so calls track spend), not by wall-clock
`minutes`. A usage line without `tool_calls` falls back to its `minutes` (legacy / QA lines); any `minutes` over 600 or
`tool_calls` over 1000 is bad data: dropped and reported on a `warning:` line of the ledger comment.

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
USAGE_LINE_RE = re.compile(r"^tlwu-usage:.*$", re.M)
MINUTES_RE = re.compile(r"\bminutes=(\d+)(?![\d.])")
CALLS_RE = re.compile(r"\btool_calls=(\d+)(?![\d.])")
MAX_MINUTES, MAX_CALLS = 600, 1000
DROPPED: list[str] = []  # usage lines dropped as bad data in this run (reported by `warning_line`)
MODEL_CALLS: dict[str, int] = {}  # calls by builder model over the 48 h window (the `by model:` line)
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


def usage_weight(text: str | None) -> int | None:
    """#1915: weight of the LAST `tlwu-usage:` line = its tool_calls, else its minutes (legacy). None when there is no
    usage line; a bad value (minutes > 600, tool_calls > 1000, unparsable minutes) drops the line and is recorded."""
    lines = USAGE_LINE_RE.findall(text or "")
    if not lines:
        return None
    line = lines[-1]
    m, c = MINUTES_RE.search(line), CALLS_RE.search(line)
    bad = ("minutes=" in line and (not m or len(m.group(1)) > 6 or int(m.group(1)) > MAX_MINUTES)) \
        or ("tool_calls=" in line and (not c or len(c.group(1)) > 6 or int(c.group(1)) > MAX_CALLS))
    if bad:
        DROPPED.append(line[:80])
        return None
    if c:
        return int(c.group(1))
    return int(m.group(1)) if m else None


def tally_model(body: str | None, weight: int) -> None:
    """Add `weight` calls to the builder model named by the body's usage line (`unknown` when absent)."""
    found = MODEL_RE.findall(body or "")
    key = found[-1] if found else "unknown"
    MODEL_CALLS[key] = MODEL_CALLS.get(key, 0) + weight


def model_calls_line() -> str:
    keys = ["sonnet", "opus"] + sorted(k for k in MODEL_CALLS if k not in ("sonnet", "opus"))
    return "by model: " + " ".join(f"{k} {MODEL_CALLS.get(k, 0)}" for k in keys)


def warning_line() -> str:
    return f"warning: dropped {len(DROPPED)} usage line(s) as bad data (minutes > {MAX_MINUTES} or tool_calls > {MAX_CALLS})" \
        if DROPPED else ""


def build_rows(minutes: dict[str, int], mix: dict[str, int]) -> list[dict]:
    """Rows type|calls (key `minutes`, kept for callers)|share|target|deficit; share over the total of all rows, 0 when the total is 0."""
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
    return f"owner-requested (src:owner, exempt from mix): {owner48} calls (48 h)"


def infra_line(infra_min: int, total_min: int) -> str:
    share = round(100 * infra_min / total_min, 1) if total_min else 0.0
    return f"infra: {infra_min} / {total_min} = {share}% (cap 10%)"


def render(rows: list[dict], infra: str, total: int, untyped: int, mix_ok: bool, now: datetime) -> str:
    out = [f"Ledger (48 h to {now:%H:%M}Z, PRs merged to dev + QA/idea usage comments, total {total} calls; "
           f"untyped PRs excluded: {untyped} calls)"]
    if not mix_ok:
        out.append("mix invalid, default used")
    out += ["", "| type | calls | share | target | deficit |", "|---|---|---|---|---|"]
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
    """Weight (#1915: tool_calls, else minutes) from the LAST non-empty line of a PR body only (rules section 10)."""
    lines = [l for l in (body or "").splitlines() if l.strip()]
    return usage_weight(lines[-1]) if lines else None


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
        if _ts(merged) >= since48:
            tally_model(pr.get("body"), mins)
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
        m = usage_weight(c.get("body"))
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
    return usage_weight(found[-1]) if found else None


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
        if t >= since48:
            tally_model(c.get("body"), mins)
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
    return f"open->merge fix calls (48 h): {fix48} ({share}% of total)"


# --- #1676: per-model quality metrics over the last 14 days (owner a1: decide model pairings by data) -------------
MODEL_DAYS = 14
INCIDENT_DAYS = 7
MODEL_RE = re.compile(r"^tlwu-usage:.*\bmodel=([\w.\-]+)", re.M)
TOKENS_RE = re.compile(r"^tlwu-usage:.*\btokens_in=(\d+)\b.*\btokens_out=(\d+)\b", re.M)  # #1916
REVIEW_FAIL_RE = re.compile(r"AI review: FAIL|Opus review: CHANGES")
REVERT_RE = re.compile(r"\brevert", re.I)


def pr_model(body: str | None) -> str:
    """Builder model from the LAST non-empty line of a PR body (`tlwu-usage: model=<m> ...`); `unknown` when absent."""
    lines = [l for l in (body or "").splitlines() if l.strip()]
    m = MODEL_RE.search(lines[-1]) if lines else None
    return m.group(1) if m else "unknown"


def pr_tokens(body: str | None) -> tuple[int, int] | None:
    """(tokens_in, tokens_out) from the LAST non-empty line of a PR body; None when the fields are missing (#1916)."""
    lines = [l for l in (body or "").splitlines() if l.strip()]
    m = TOKENS_RE.search(lines[-1]) if lines else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def token_rows(prs: list[dict]) -> list[dict]:
    """prs: dicts with model and optional tokens (tuple). One row per model that has any; PRs without tokens are ignored."""
    by: dict[str, list[tuple[int, int]]] = {}
    for p in prs:
        if p.get("tokens"):
            by.setdefault(p["model"], []).append(p["tokens"])
    return [{"model": m, "prs": len(t), "in_avg": round(sum(x[0] for x in t) / len(t)),
             "out_avg": round(sum(x[1] for x in t) / len(t))} for m, t in sorted(by.items())]


def render_tokens(rows: list[dict]) -> str:
    """Empty string when no PR carries token fields, so the comment is unchanged until workers start writing them."""
    if not rows:
        return ""
    out = ["Tokens per merged PR by builder model (only PRs whose usage line has tokens_in / tokens_out)", "",
           "| model | PRs with tokens | avg tokens_in | avg tokens_out |", "|---|---|---|---|"]
    out += [f"| {r['model']} | {r['prs']} | {r['in_avg']} | {r['out_avg']} |" for r in rows]
    return "\n".join(out)


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
        out.append({"model": pr_model(p.get("body")), "tokens": pr_tokens(p.get("body")), "review_fail": has_review_fail(comments),
                    "red": red_rounds(conclusions),
                    "incident": is_incident(n, linked_issue(p["head"]["ref"], p.get("body")), merged, issues, later)})
    return out


# --- #1919: ticket supply. ready = pickable, startable = pickable and free to begin now, blocked_by_dep = waits on
# another open issue / PR. S-split reads `target` (2 x workers that ran in the last 24 h) and splits until met.
POOL_SKIP = {"needs-triage", "needs-owner", "needs-opus", "status:blocked", "status:proposal", "status:frozen",
             "status:doing", "status:review"}
DEP_RE = re.compile(r"^\s*(?:depends on|blocked by|after)\b[^\n]*", re.I | re.M)
NUM_RE = re.compile(r"#(\d+)(?!\d)")
WORKER_RE = re.compile(r"^\s*(W-hard|S-hard|S-easy(?:[\s-]*(?:slot\s*)?[A-C])?|slot\s*[A-C])\b", re.I)
WORKERS_HOURS = 24


def deps(body: str | None) -> set[int]:
    """Issue / PR numbers a body says it waits on: lines starting `Depends on:` / `Blocked by:` / `after #M`."""
    return {int(n) for line in DEP_RE.findall(body or "") for n in NUM_RE.findall(line)}


def pool_counts(issues: list[dict], prs: list[dict]) -> dict[str, int]:
    """issues: open issues (REST shape; PR entries are skipped); prs: open dev PRs. `ready` = status:todo + ok-to-work and
    no hold label; `startable` = ready with no open linked PR and no open dependency; `blocked_by_dep` = any open issue
    (ready or status:blocked) whose stated dependency is still open."""
    real = [i for i in issues if "pull_request" not in i]
    open_nums = {i["number"] for i in issues} | {p["number"] for p in prs}
    with_pr = {n for p in prs if (n := linked_issue(p["head"]["ref"], p.get("body"))) is not None}
    ready = startable = blocked = 0
    for i in real:
        labels = {l["name"] for l in i.get("labels", [])}
        waits = bool((deps(i.get("body")) - {i["number"]}) & open_nums)
        blocked += waits
        if {"status:todo", "ok-to-work"} <= labels and not labels & POOL_SKIP:
            ready += 1
            startable += not waits and i["number"] not in with_pr
    return {"ready": ready, "startable": startable, "blocked_by_dep": blocked}


def worker_key(text: str | None) -> str | None:
    """Normalised worker role from a #61 report line (`S-easy slot A:` / `S-easy-A` -> `S-easy A`); None for others."""
    m = WORKER_RE.match(text or "")
    if not m:
        return None
    s = m.group(1).lower()
    if s.endswith("hard"):
        return "W-hard" if s.startswith("w") else "S-hard"
    slot = re.search(r"[abc]$", s)  # bare `S-easy` is slot A (the :13 slot)
    return f"S-easy {slot.group(0).upper() if slot else 'A'}"


def workers_ran(comments: list[dict], prs: list[dict], now: datetime) -> int:
    """Distinct workers in the last 24 h: cloud worker roles reporting on #61 (by PRODUCER) plus each other author of
    a dev PR opened in that window (contributor machines)."""
    since = now - timedelta(hours=WORKERS_HOURS)
    roles = {k for c in comments if (c.get("user") or {}).get("login") == PRODUCER
             and since <= _ts(c["created_at"]) <= now and (k := worker_key(c.get("body")))}
    people = {(p.get("user") or {}).get("login") for p in prs if since <= _ts(p["created_at"]) <= now}
    return len(roles) + len(people - {PRODUCER, None})


def pool_line(counts: dict[str, int], workers: int) -> str:
    return (f"ready={counts['ready']} startable={counts['startable']} blocked_by_dep={counts['blocked_by_dep']} "
            f"workers_24h={workers} target={2 * workers}")


def collect_pool(now: datetime) -> str:
    issues = _gh_list(f"repos/{REPO}/issues?state=open&per_page=100")
    open_prs = _gh_list(f"repos/{REPO}/pulls?state=open&base=dev&per_page=100")
    since = now - timedelta(hours=WORKERS_HOURS)
    recent: list = []
    for page in range(1, 11):  # newest first; stop at the first page that reaches past the 24 h window
        batch = _gh(f"repos/{REPO}/pulls?state=all&base=dev&sort=created&direction=desc&per_page=100&page={page}") or []
        recent += batch
        if len(batch) < 100 or _ts(batch[-1]["created_at"]) < since:
            break
    comments = _gh_list(f"repos/{REPO}/issues/{LOG_ISSUE}/comments?since={since:%Y-%m-%dT%H:%M:%SZ}&per_page=100")
    return pool_line(pool_counts(issues, open_prs), workers_ran(comments, recent, now))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now = datetime.now(UTC)
    DROPPED.clear()
    MODEL_CALLS.clear()
    mix, ok = parse_mix(_gh(f"repos/{REPO}/issues/{THROTTLE_ISSUE}")["body"])
    by_type, untyped, infra_min, total7 = collect(now)
    f_type, f_untyped, f_infra, f_total7, fix48 = collect_fix(now)
    for t, m in f_type.items():
        by_type[t] = by_type.get(t, 0) + m
    untyped, infra_min, total7 = untyped + f_untyped, infra_min + f_infra, total7 + f_total7
    rows = build_rows(by_type, mix)
    total = sum(r["minutes"] for r in rows)
    infra = infra_line(infra_min, total7) + "\n" + fix_line(fix48, total + untyped)
    infra += "\n" + owner_line(by_type.get(OWNER_KEY, 0)) + "\n" + model_calls_line()
    infra += "\n" + collect_pool(now)
    if DROPPED:
        infra += "\n" + warning_line()
    text = render(rows, infra, total, untyped, ok, now)
    models = collect_models(now)
    tok = render_tokens(token_rows(models))
    text = text.replace("\n\n_posted by", "\n\n" + render_models(model_rows(models)) + (f"\n\n{tok}" if tok else "") + "\n\n_posted by", 1)
    if args.dry_run:
        print(text)
        return 0
    subprocess.run(["gh", "api", f"repos/{REPO}/issues/{LOG_ISSUE}/comments", "-f", f"body={text}"], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
