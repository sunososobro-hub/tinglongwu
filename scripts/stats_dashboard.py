#!/usr/bin/env python3
"""Hourly stats dashboard (stdlib only, GITHUB_TOKEN only, no telemetry).

Usage: python scripts/stats_dashboard.py OUTDIR --repo OWNER/NAME [--branch dev]

Reads OUTDIR/data.json (hourly snapshots, optional), appends one snapshot, rebuilds the
full-history series from the API (series.json) and writes a self-contained index.html.
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone, UTC

KEEP = 24 * 90  # hourly snapshots kept (~90 days)
WINDOW_H = 72


def http_api(path, accept="application/vnd.github+json"):
    """GET https://api.github.com<path> -> parsed JSON (uses GITHUB_TOKEN if set)."""
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Accept", accept)
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _pages(api, path, limit=100, accept=None):
    sep = "&" if "?" in path else "?"
    out = []
    for page in range(1, limit + 1):
        url = "%s%sper_page=100&page=%d" % (path, sep, page)
        chunk = api(url, accept) if accept else api(url)
        out.extend(chunk)
        if len(chunk) < 100:
            break
    return out


STAR = "application/vnd.github.star+json"
GROUPS = ("status", "priority", "area", "os", "src", "model", "mode", "lang", "milestone")  # mode/lang: #585


def day_of(ts):
    return ts[:10]


def day_range(start, end):
    """Inclusive list of YYYY-MM-DD strings from start to end."""
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    out = []
    while d0 <= d1:
        out.append(d0.strftime("%Y-%m-%d"))
        d0 += timedelta(days=1)
    return out


def per_day(timestamps, days):
    """Count of timestamps falling on each day in `days`."""
    c = {}
    for t in timestamps:
        c[day_of(t)] = c.get(day_of(t), 0) + 1
    return [c.get(d, 0) for d in days]


def cumulative(timestamps, days):
    """Running total at the end of each day (events before days[0] count too)."""
    total = sum(1 for t in timestamps if day_of(t) < days[0])
    out = []
    for n in per_day(timestamps, days):
        total += n
        out.append(total)
    return out


def open_over_time(issues, days):
    """Open-issue count at end of each day, from created_at / closed_at (no PRs)."""
    out = []
    for d in days:
        n = 0
        for i in issues:
            if day_of(i["created_at"]) <= d and not (i.get("closed_at") and day_of(i["closed_at"]) <= d):
                n += 1
        out.append(n)
    return out


def group_of(token):
    """Map a label (or 'milestone:<title>') to (group, value); None if ungrouped."""
    if token.startswith("milestone:"):
        return "milestone", token[len("milestone:"):]
    if len(token) == 2 and token[0] == "P" and token[1] in "01234":
        return "priority", token
    if ":" in token:
        g, v = token.split(":", 1)
        if g in ("status", "area", "os", "src", "model", "mode", "lang"):
            return g, v
    return None


def tokens_at(final, events, cutoff_day):
    """Label/milestone token set of one issue at the end of cutoff_day.

    events: [(ts, "add"|"remove", token)]. Start from the final state, undo all
    events to get the creation state, then replay those up to cutoff_day.
    """
    evs = sorted(events)
    cur = set(final)
    for _, kind, tok in reversed(evs):
        if kind == "add":
            cur.discard(tok)
        else:
            cur.add(tok)
    for ts, kind, tok in evs:
        if day_of(ts) > cutoff_day:
            break
        if kind == "add":
            cur.add(tok)
        else:
            cur.discard(tok)
    return cur


def milestone_token(title):
    return "milestone:" + title


def collect_token_events(raw_events):
    """Repo-wide issue events -> {issue_number: [(ts, kind, token)]} (no PRs)."""
    out = {}
    for e in raw_events:
        iss = e.get("issue") or {}
        if "pull_request" in iss or "number" not in iss:
            continue
        ev = e.get("event")
        if ev in ("labeled", "unlabeled") and e.get("label"):
            tok = e["label"]["name"]
        elif ev in ("milestoned", "demilestoned") and e.get("milestone"):
            tok = milestone_token(e["milestone"]["title"])
        else:
            continue
        kind = "add" if ev in ("labeled", "milestoned") else "remove"
        out.setdefault(iss["number"], []).append((e["created_at"], kind, tok))
    return out


def final_tokens(issue):
    t = {lb["name"] for lb in issue.get("labels", [])}
    if issue.get("milestone"):
        t.add(milestone_token(issue["milestone"]["title"]))
    return t


def group_series(issues, token_events, days, group):
    """{value: [open-issue count per day]} for one dimension."""
    vals = {}
    for di, d in enumerate(days):
        for i in issues:
            if day_of(i["created_at"]) > d or (i.get("closed_at") and day_of(i["closed_at"]) <= d):
                continue
            toks = tokens_at(final_tokens(i), token_events.get(i["number"], []), d)
            found = set()
            for t in toks:
                g = group_of(t)
                if g and g[0] == group:
                    found.add(g[1])
            if not found:
                found = {"(none)"}
            for v in found:
                vals.setdefault(v, [0] * len(days))[di] += 1
    return vals


def parse_ts(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def median_hours(pairs, since=None):
    """Median hours between (start, end) ISO timestamps; only ends >= since (datetime) if given."""
    vals = []
    for a, b in pairs:
        if since is not None and parse_ts(b) < since:
            continue
        vals.append((parse_ts(b) - parse_ts(a)).total_seconds() / 3600.0)
    vals.sort()
    if not vals:
        return None
    m = len(vals) // 2
    return round(vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2, 2)


def percentile_hours(pairs, pct, since=None):
    """Nearest-rank percentile (hours) of (start, end) durations; ends >= since only."""
    import math
    vals = sorted((parse_ts(b) - parse_ts(a)).total_seconds() / 3600.0
                  for a, b in pairs if since is None or parse_ts(b) >= since)
    if not vals:
        return None
    return round(vals[max(1, math.ceil(pct / 100.0 * len(vals))) - 1], 2)


def count_since(timestamps, since):
    return sum(1 for t in timestamps if parse_ts(t) >= since)


def hero_stats(merged, closed, commits, created, now):
    """Today (UTC day) / last 24h / total counts for PRs merged, issues closed, commits."""
    today = now.strftime("%Y-%m-%d")
    d24 = now - timedelta(hours=24)
    out = {"day_n": (now.date() - parse_ts(created).date()).days + 1}
    for k, ts in (("prs", merged), ("issues", closed), ("commits", commits)):
        out[k] = {"today": sum(1 for t in ts if day_of(t) == today),
                  "h24": count_since(ts, d24), "total": len(ts)}
    return out


QUOTA_WEIGHT = {"opus": 2.0}  # sonnet and anything else = 1.0
TYPE_TARGETS = [("bug", 30), ("enhancement", 30), ("eval", 10), ("documentation", 5), ("maint", 5)]
SIDE_TARGETS = [("QA", 15), ("ideas", 5)]  # fixed-schedule rows, informational
FLOW_HOURS = 48  # same window as the S-hard ledger
BACKFILL_DAYS = 14  # #662 part 4: path-inferred dimensions re-applied to this many days of merged PRs
UNLABELLED = "unlabelled"


def parse_usage(body):
    """(model, minutes) from the last `tlwu-usage:` line of a PR body, else None."""
    for line in reversed((body or "").splitlines()):
        line = line.strip()
        if line.startswith("tlwu-usage:"):
            m = re.search(r"model=(\w+)", line)
            n = re.search(r"minutes=(\d+(?:\.\d+)?)", line)
            if m and n:
                return m.group(1).lower(), float(n.group(1))
            return None
    return None


def linked_issue(body):
    m = re.search(r"(?i)\b(?:closes|fixes|resolves|refs)\s+#(\d+)", body or "")
    return int(m.group(1)) if m else None


def parse_faucet(body):
    """{'level': int, 'focus': [str]} from #574's body; None if the level line is unreadable."""
    lines = (body or "").splitlines()
    m = re.match(r"\s*level:\s*(\d+)", lines[0]) if lines else None
    if not m:
        return None
    focus = []
    for ln in lines[1:]:
        f = re.match(r"\s*focus:\s*(.*)", ln)
        if f:
            focus = [x for x in re.split(r"[\s,/+]+", f.group(1).strip()) if x]
            break
    return {"level": int(m.group(1)), "focus": focus}


ALL = "all"  # rules section 12: no os/lang label = all platforms / all languages


def _label_value(labels, prefix, default=UNLABELLED):
    for name in labels:
        if name.startswith(prefix):
            return name[len(prefix):]
    return default


def _platform(labels):
    p = _label_value(labels, "os:", None)
    if p is not None:
        return p
    return "mobile" if "area:mobile" in labels else ALL


# #662: path -> dimension rules, used only when the linked issue has no label for that dimension.
_MODE_RULES = (("speak", ("tts", "reverse", "speak")), ("listen", ("asr", "segment")))
_OS_RULES = (("windows", ("installer/", ".iss", "capture/wasapi")),
             ("mac", ("mac_install.sh", "capture/macos")),
             ("linux", ("install.sh", "capture/pulse")))
_LANG_RE = re.compile(r"(?:flores|eval)[^/]*?[_/-](zh|en|ja|ko|ru|hi|yue|vi|th|id|es|de|fr|it|pt)(?:[_.-]|$)")


def infer_dims(files):
    """Changed file paths -> {'mode'|'os'|'lang': value} inferred from paths (missing key = no inference)."""
    out = {}
    for f in files:
        f = f.lower()
        base = f.rsplit("/", 1)[-1]
        if f.startswith("mobile/"):
            out.setdefault("os", "mobile")
        for value, keys in _OS_RULES:
            if any(k in f for k in keys if "/" in k or k.startswith(".")) or base in keys:
                out.setdefault("os", value)
        for value, keys in _MODE_RULES:
            if any(base.startswith(k) or "/" + k in f for k in keys):
                out.setdefault("mode", value)
        m = _LANG_RE.search(f)
        if m:
            out.setdefault("lang", m.group(1))
    return out


INFRA = "infrastructure"  # #662 part 2: the mode:shared bucket, named for what it is and split by area


def infra_node(labels):
    """mode:shared work -> 'infrastructure <area>' (first area:* label), or plain 'infrastructure' with no area."""
    area = _label_value(labels, "area:", None)
    return "%s %s" % (INFRA, area) if area else INFRA


def build_flow(items):
    """items: [{'model','minutes','labels':[names]}] -> quota-flow data.

    Everything is in quota minutes (minutes x model weight, opus = 2x). A missing mode label goes to
    'unlabelled'; mode:shared = 'infrastructure <area>' (#662); no os label = 'mobile' for area:mobile else 'all';
    no lang label = 'all'.
    """
    paths, types = {}, {}
    inferred = {"mode": 0.0, "os": 0.0, "lang": 0.0}
    for it in items:
        q = it["minutes"] * QUOTA_WEIGHT.get(it["model"], 1.0)
        if q <= 0:
            continue
        labels = it.get("labels", [])
        mode, plat, lang = _label_value(labels, "mode:"), _platform(labels), _label_value(labels, "lang:", ALL)
        guess = infer_dims(it.get("files", []))
        if mode == UNLABELLED and "mode" in guess:
            mode = guess["mode"]
            inferred["mode"] += q
        if plat == ALL and "os" in guess and not _label_value(labels, "os:", None):
            plat = guess["os"]
            inferred["os"] += q
        if lang == ALL and "lang" in guess and not _label_value(labels, "lang:", None):
            lang = guess["lang"]
            inferred["lang"] += q
        if mode == "shared":
            mode = infra_node(labels)
        key = (mode, plat, lang)
        paths[key] = paths.get(key, 0.0) + q
        t = next((n for n, _ in TYPE_TARGETS if n in labels), "other")
        types[t] = types.get(t, 0.0) + q
    total = sum(paths.values())

    def pct(v):
        return 100.0 * v / total if total else 0.0

    layers = []
    for idx in range(3):
        node = {}
        for k, v in paths.items():
            node[k[idx]] = node.get(k[idx], 0.0) + v
        layers.append([{"name": n, "quota": v, "share": pct(v)}
                       for n, v in sorted(node.items(), key=lambda kv: (-kv[1], kv[0]))])
    mix = [{"type": n, "quota": types.get(n, 0.0), "share": pct(types.get(n, 0.0)), "target": t,
            "delta": pct(types.get(n, 0.0)) - t} for n, t in TYPE_TARGETS]
    if types.get("other"):
        mix.append({"type": "other", "quota": types["other"], "share": pct(types["other"]),
                    "target": 0, "delta": pct(types["other"])})
    return {"total": total, "paths": [[*k, v] for k, v in sorted(paths.items())],
            "modes": layers[0], "platforms": layers[1], "langs": layers[2],
            "inferred": {k: pct(v) for k, v in inferred.items()},
            "unclassified": pct(sum(v for k, v in paths.items() if k[0] == UNLABELLED)),
            "type_mix": mix, "side": [{"type": n, "target": t} for n, t in SIDE_TARGETS]}


def build_flow_inputs(api, repo, merged_prs, now, issue_labels=None, files_cache=None):
    """Quota flow from recent merged PRs' usage lines + linked-issue labels, and the #574 faucet.

    issue_labels: {issue number: [label names]} from the already-fetched issue list (no per-issue call).
    files_cache: {str(pr number): [filenames]}; a merged PR's files never change, so only PRs missing from
    it are fetched and added (#662: keeps the 14-day backfill to a handful of calls per hourly run).
    """
    since = now - timedelta(days=BACKFILL_DAYS)  # fetch once for the longest window, filter per window below
    items, cache = [], dict(issue_labels or {})
    files_cache = {} if files_cache is None else files_cache
    for p in merged_prs:
        usage = parse_usage(p.get("body"))
        if not usage or parse_ts(p["merged_at"]) < since:
            continue
        n = linked_issue(p.get("body"))
        if n is not None and n not in cache:
            try:
                cache[n] = [lb["name"] for lb in api("/repos/%s/issues/%d" % (repo, n)).get("labels", [])]
            except Exception as e:  # unlabelled node instead of failing
                print("issue #%d unavailable: %s" % (n, e), file=sys.stderr)
                cache[n] = []
        key = str(p.get("number"))
        if key in files_cache:
            files = files_cache[key]
        else:
            try:
                files = [f["filename"] for f in api("/repos/%s/pulls/%d/files?per_page=100" % (repo, p["number"]))]
                files_cache[key] = files
            except Exception as e:  # no inference for this PR; retried next run
                print("pr #%s files unavailable: %s" % (p.get("number"), e), file=sys.stderr)
                files = []
        items.append({"model": usage[0], "minutes": usage[1], "labels": cache.get(n, []), "files": files,
                      "merged_at": p["merged_at"]})
    try:
        faucet = parse_faucet(api("/repos/%s/issues/574" % repo).get("body"))
    except Exception as e:
        print("faucet unavailable: %s" % e, file=sys.stderr)
        faucet = None
    recent = [i for i in items if parse_ts(i["merged_at"]) >= now - timedelta(hours=FLOW_HOURS)]
    flow = build_flow(recent)
    flow["backfill"] = {"days": BACKFILL_DAYS, **{k: v for k, v in build_flow(items).items()
                                                  if k in ("total", "inferred", "unclassified")}}
    return flow, faucet


_FETCH = object()  # sentinel: fetch releases inside the builder
PR_FILES = "pr_files.json"  # #662: per-PR changed-file cache, kept on gh-pages next to data.json
PR_FILES_KEEP = 1000  # newest PR numbers kept; 14 days was ~400 merged PRs in 2026-11


def prune_files_cache(files_cache):
    """Keep the newest PR_FILES_KEEP entries so the cache file stays small."""
    keep = sorted(files_cache, key=lambda k: int(k) if str(k).isdigit() else -1)[-PR_FILES_KEEP:]
    return {k: files_cache[k] for k in keep}


def release_downloads(api, repo):
    """[{'tag','downloads'}] newest first (sum of asset download_count); None if unavailable."""
    try:
        rels = _pages(api, "/repos/%s/releases" % repo)
    except Exception as e:  # dashboard still builds without downloads
        print("releases unavailable: %s" % e, file=sys.stderr)
        return None
    out = []
    for r in rels:
        d = {"tag": r.get("tag_name", "?"), "downloads": sum(a.get("download_count", 0) for a in r.get("assets", []))}
        if r.get("published_at") and not r.get("draft"):
            d["published_at"] = r["published_at"]  # #744 release-time table
        out.append(d)
    return out


# ------------------------------------------------------------------ #744: does quota turn into output?
TPE = timezone(timedelta(hours=8))  # rules: days and weeks are Asia/Taipei
TREND_DAYS = 14
MARKER_PR = 743  # #742 rules speed-up merged here: before/after marker on the trend charts
RELEASE_ROWS = 10


def tpe_day(ts):
    return parse_ts(ts).astimezone(TPE).strftime("%Y-%m-%d")


def quota_trend(merged_prs, now, n_days=TREND_DAYS, marker_pr=MARKER_PR):
    """Per Asia/Taipei day (last n_days): quota minutes (tlwu-usage minutes x model weight) and PRs merged to the
    base branch, plus merged PRs per 100 quota minutes (None on a day with no quota). `marker` = the Taipei day the
    marker PR merged (None when it is outside the window)."""
    end = now.astimezone(TPE).date()
    days = [(end - timedelta(days=n_days - 1 - i)).isoformat() for i in range(n_days)]
    quota, merged = dict.fromkeys(days, 0.0), dict.fromkeys(days, 0)
    marker = None
    for p in merged_prs:
        d = tpe_day(p["merged_at"])
        if p.get("number") == marker_pr:
            marker = d
        if d not in merged:
            continue
        merged[d] += 1
        u = parse_usage(p.get("body"))
        if u:
            quota[d] += u[1] * QUOTA_WEIGHT.get(u[0], 1.0)
    return {"days": days, "quota": [round(quota[d], 1) for d in days], "merged": [merged[d] for d in days],
            "per100": [round(100.0 * merged[d] / quota[d], 2) if quota[d] else None for d in days],
            "marker": marker if marker in quota else None, "marker_pr": marker_pr}


def _minutes(a, b):
    return round((parse_ts(b) - parse_ts(a)).total_seconds() / 60.0, 1)


def release_times(rels, runs, items, limit=RELEASE_ROWS):
    """One row per published release (newest first, last `limit`): release.yml wall time (run start -> run end of the
    successful run that was running when the release was published) and release PR open -> published (the
    `release: <tag>` PR, preferring the merged one, opened before publishing). Missing data -> None."""
    rows = []
    for r in sorted((r for r in rels or () if r.get("published_at")), key=lambda r: r["published_at"], reverse=True)[:limit]:
        pub = parse_ts(r["published_at"])
        runs_at = [x for x in runs or () if x.get("conclusion") == "success" and x.get("run_started_at")
                   and parse_ts(x["run_started_at"]) <= pub <= parse_ts(x["updated_at"]) + timedelta(minutes=5)]
        run = max(runs_at, key=lambda x: x["run_started_at"], default=None)
        title = "release: " + r["tag"]
        prs = [i for i in items or () if "pull_request" in i and (i.get("title") or "").strip() == title
               and parse_ts(i["created_at"]) <= pub]
        merged = [i for i in prs if (i.get("pull_request") or {}).get("merged_at")]
        pr = max(merged or prs, key=lambda i: i["created_at"], default=None)
        rows.append({"tag": r["tag"], "published_at": r["published_at"],
                     "run_min": _minutes(run["run_started_at"], run["updated_at"]) if run else None,
                     "pr": pr["number"] if pr else None,
                     "pr_min": _minutes(pr["created_at"], r["published_at"]) if pr else None})
    return rows


def release_runs(api, repo):
    try:
        return api("/repos/%s/actions/workflows/release.yml/runs?per_page=100" % repo).get("workflow_runs", [])
    except Exception as e:  # table shows n/a instead of failing the page
        print("release.yml runs unavailable: %s" % e, file=sys.stderr)
        return None


INSTALL_DAYS = 14  # #806: days of active-install counts shown


def counter_base(env=None):
    """#806 counter Worker base URL: env TLWU_UPDATE_COUNTER, else tinglongwu_setup.selfupdate.COUNTER_BASE."""
    env = os.environ if env is None else env
    if env.get("TLWU_UPDATE_COUNTER"):
        return env["TLWU_UPDATE_COUNTER"].rstrip("/")
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from tinglongwu_setup.selfupdate import COUNTER_BASE
        return COUNTER_BASE.rstrip("/")
    except Exception:
        return ""


def valid_install_rows(rows):
    """rows if it is a list of {date, version, os: str, n: integer-like} objects; ValueError otherwise."""
    if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
        raise ValueError("counter response is not a list of rows")
    for x in rows:
        if not all(isinstance(x.get(k), str) for k in ("date", "version", "os")):
            raise ValueError("counter row needs string date/version/os")
        if isinstance(x.get("n"), bool) or not isinstance(x.get("n", 0), (int, float, str)):
            raise ValueError("counter row has a non-numeric count")
        int(x.get("n") or 0)  # ValueError for a non-numeric string
    return rows


def fetch_installs(base):
    """Rows [{date, version, os, n}] from the counter Worker's /v1/stats, or None (no Worker, unreachable)."""
    if not base.startswith("https://"):
        return None
    try:
        req = urllib.request.Request(base + "/v1/stats", headers={"User-Agent": "tlwu-stats"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return valid_install_rows(json.load(r))
    except Exception as e:  # dashboard still builds without the counter
        print("install counter unavailable: %s" % e, file=sys.stderr)
        return None


def active_installs(rows, now):
    """Update checks run at most once a day per install, so checks per day ~ active installs that day.
    -> {'days', 'total' (per day), 'versions': [{'version', 'os', 'last_day', 'week_avg'}]} over complete UTC days."""
    if rows is None:
        return None
    end = (now - timedelta(days=1)).strftime("%Y-%m-%d")  # last complete day
    days = day_range((now - timedelta(days=INSTALL_DAYS)).strftime("%Y-%m-%d"), end)
    week = set(days[-7:])
    total = dict.fromkeys(days, 0)
    per = {}
    for r in rows:
        d, n = r.get("date"), int(r.get("n") or 0)
        if d in total:
            total[d] += n
        if d in week:
            v = per.setdefault((str(r.get("version")), str(r.get("os"))), {"last_day": 0, "week": 0})
            v["week"] += n
            if d == end:
                v["last_day"] += n
    versions = [{"version": k[0], "os": k[1], "last_day": v["last_day"], "week_avg": round(v["week"] / 7.0, 1)}
                for k, v in sorted(per.items(), key=lambda kv: (-kv[1]["week"], kv[0]))]
    return {"days": days, "total": [total[d] for d in days], "versions": versions}


def downloads_series(history, days, current=None):
    """Per-day total downloads: last snapshot value up to that day, None before the first one.

    GitHub only exposes a running total, so the trend comes from the hourly snapshots;
    `current` (the live total) overrides the last day.
    """
    known = sorted((h["ts"], h["downloads"]) for h in history or () if h.get("downloads") is not None)
    out, k, last = [], 0, None
    for d in days:
        while k < len(known) and day_of(known[k][0]) <= d:
            last = known[k][1]
            k += 1
        out.append(last)
    if current is not None and out:
        out[-1] = current
    return out


TTFS_POINTS = 10  # #777: last N install-clean values shown per OS
TTFS_BUDGET_S = 180  # target: install start -> first subtitle, excluding the model download (scripts/ttfs.py)
TTFS_RUNS = 30  # successful install-clean runs scanned for ttfs-<os> artifacts
_TTFS_FIELDS = ("excl_download_seconds", "os", "version", "date")


def ttfs_points(docs, points=TTFS_POINTS):
    """docs: parsed ttfs.json dicts (schema in #777) -> {os: [newest `points` rows, oldest first]}.

    Pure: malformed docs (missing fields, null excl_download_seconds) are skipped."""
    by_os = {}
    for idx, d in enumerate(docs or ()):  # docs are newest-first: idx breaks ties between equal dates
        if not isinstance(d, dict) or any(d.get(k) is None for k in _TTFS_FIELDS):
            continue
        try:
            sec = float(d["excl_download_seconds"])
        except (TypeError, ValueError):
            continue
        by_os.setdefault(str(d["os"]), []).append(
            {"version": str(d["version"]), "date": str(d["date"]), "seconds": round(sec, 1), "sha": str(d.get("sha") or ""), "_i": idx})
    out = {}
    for o, rows in sorted(by_os.items()):
        keep = sorted(rows, key=lambda r: (r["date"], -r["_i"]))[-points:]  # oldest first, newest last
        out[o] = [{k: v for k, v in r.items() if k != "_i"} for r in keep]
    return out


def http_artifact_json(url):
    """Download a GitHub artifact zip (GITHUB_TOKEN only) and return the parsed ttfs.json inside it."""
    class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            nr = super().redirect_request(req, fp, code, msg, headers, newurl)
            if nr is not None:
                nr.headers.pop("Authorization", None)  # the blob host must not get the token
            return nr

    req = urllib.request.Request(url)
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.build_opener(_NoAuthOnRedirect).open(req, timeout=60) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    name = next(n for n in z.namelist() if n.endswith("ttfs.json"))
    return json.loads(z.read(name))


def collect_ttfs(api, repo, fetch=http_artifact_json):
    """Newest-first successful install-clean runs -> their `ttfs-<os>` artifacts -> {os: rows}. Failures -> {}."""
    docs = []
    try:
        runs = api("/repos/%s/actions/workflows/install-clean.yml/runs?status=success&per_page=%d"
                   % (repo, TTFS_RUNS)).get("workflow_runs", [])
        for run in runs:
            try:
                arts = api("/repos/%s/actions/runs/%d/artifacts" % (repo, run["id"])).get("artifacts", [])
            except Exception as e:  # one bad run must not hide the later ones
                print("artifacts of run %s unavailable: %s" % (run.get("id"), e), file=sys.stderr)
                continue
            for a in arts:
                if a.get("name", "").startswith("ttfs-") and not a.get("expired"):
                    try:
                        docs.append(fetch(a["archive_download_url"]))
                    except Exception as e:
                        print("ttfs artifact %s unavailable: %s" % (a.get("name"), e), file=sys.stderr)
    except Exception as e:  # page shows "no data yet" instead of failing
        print("install-clean runs unavailable: %s" % e, file=sys.stderr)
    return ttfs_points(docs)


LANGS_BRANCH = "eval-results"  # #643: eval-langs.yml weekly writes langs/YYYY-MM-DD.json there


def lang_verdict_rows(latest, previous=None):
    """#643: rows `language | ASR | MT | verdict | change vs last week` from the latest two weekly results.
    ASR = error rate in %, MT = chrF x100, both to two decimals; deltas are latest - previous (None if either is
    missing). Malformed rows are skipped."""
    def by_pair(doc):
        out = {}
        for r in (doc or {}).get("languages", []) if isinstance(doc, dict) else []:
            if isinstance(r, dict) and isinstance(r.get("pair"), str):
                out[r["pair"]] = r
        return out

    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def asr(r):
        v = num((r or {}).get("asr_error_rate"))
        return None if v is None else round(v * 100, 2)

    def mt(r):
        v = num((r or {}).get("chrf"))
        return None if v is None else round(v, 2)

    def delta(a, b):
        return None if a is None or b is None else round(a - b, 2)

    prev = by_pair(previous)
    rows = []
    for pair, r in by_pair(latest).items():
        p = prev.get(pair)
        a, m = asr(r), mt(r)
        rows.append({"language": pair, "asr": a, "mt": m,
                     "verdict": "PASS" if r.get("verdict") == "PASS" else "FAIL",
                     "d_asr": delta(a, asr(p)), "d_mt": delta(m, mt(p))})
    return rows


def collect_lang_verdicts(api, repo):
    """{date, previous_date, rows} from the two newest langs/*.json on eval-results; None when unavailable."""
    import base64
    try:
        files = sorted(f["name"] for f in api("/repos/%s/contents/langs?ref=%s" % (repo, LANGS_BRANCH))
                       if isinstance(f, dict) and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f.get("name", "")))
        if not files:
            return None
        docs = [json.loads(base64.b64decode(api("/repos/%s/contents/langs/%s?ref=%s"
                                                   % (repo, n, LANGS_BRANCH))["content"]))
                for n in files[-2:]]
    except Exception as e:  # no eval-results yet / API hiccup: the section says "no data yet"
        print("weekly language results unavailable: %s" % e, file=sys.stderr)
        return None
    latest, previous = docs[-1], (docs[0] if len(docs) == 2 else None)
    return {"date": files[-1][:-5], "previous_date": files[-2][:-5] if len(files) > 1 else None,
            "rows": lang_verdict_rows(latest, previous)}


def build_series(api, repo, branch, now, history=None, rels=_FETCH, files_cache=None):
    info = api("/repos/%s" % repo)
    created = info["created_at"]
    days = day_range(day_of(created), now.strftime("%Y-%m-%d"))
    all_issues = _pages(api, "/repos/%s/issues?state=all" % repo)
    issues = [i for i in all_issues if "pull_request" not in i]
    commits = _pages(api, "/repos/%s/commits?sha=%s" % (repo, branch))
    stars = _pages(api, "/repos/%s/stargazers" % repo, accept=STAR)
    forks = _pages(api, "/repos/%s/forks" % repo)
    try:
        events = _pages(api, "/repos/%s/issues/events" % repo)
        have_events = True
    except Exception as e:  # label history degrades to current labels
        print("events unavailable: %s" % e, file=sys.stderr)
        events, have_events = [], False
    tev = collect_token_events(events)
    pulls = _pages(api, "/repos/%s/pulls?state=closed&base=%s" % (repo, branch))
    merged_prs = [p for p in pulls if p.get("merged_at")]
    pr_merged_ts = [p["merged_at"] for p in merged_prs]
    closed_issues = [i for i in issues if i.get("closed_at")]
    issue_closed_ts = [i["closed_at"] for i in closed_issues]
    commit_ts = [c["commit"]["committer"]["date"] for c in commits]
    week = now - timedelta(days=7)
    day = now - timedelta(hours=24)
    pr_pairs = [(p["created_at"], p["merged_at"]) for p in merged_prs]
    is_pairs = [(i["created_at"], i["closed_at"]) for i in closed_issues]
    try:
        ms = [{"title": m["title"], "open": m["open_issues"], "closed": m["closed_issues"]}
              for m in _pages(api, "/repos/%s/milestones?state=all" % repo)]
    except Exception:
        ms = []
    issue_labels = {i["number"]: [lb["name"] for lb in i.get("labels", [])] for i in all_issues
                    if "pull_request" not in i}
    flow, faucet = build_flow_inputs(api, repo, merged_prs, now, issue_labels, files_cache)
    if rels is _FETCH:
        rels = release_downloads(api, repo)
    dl_total = sum(r["downloads"] for r in rels) if rels is not None else None
    return {
        "flow": flow, "faucet": faucet,
        "quota_trend": quota_trend(merged_prs, now),  # #744
        "ttfs": collect_ttfs(api, repo),  # #777
        "lang_verdicts": collect_lang_verdicts(api, repo),  # #643
        "release_times": release_times(rels, release_runs(api, repo), all_issues),
        "downloads": {"total": dl_total, "releases": rels or [],
                      "series": downloads_series(history, days, dl_total) if dl_total is not None
                      else [None] * len(days)},  # releases unavailable: block stays empty
        "repo": repo, "branch": branch, "created": created,
        "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
        "commits": per_day([c["commit"]["committer"]["date"] for c in commits], days),
        "stars": cumulative([s["starred_at"] for s in stars if s.get("starred_at")], days),
        "forks": cumulative([f["created_at"] for f in forks], days),
        "open_issues": open_over_time(issues, days),
        "totals": {"stars": info.get("stargazers_count", 0), "forks": info.get("forks_count", 0),
                   "open": sum(1 for i in issues if i["state"] == "open"),
                   "commits": len(commits)},
        "groups": {g: group_series(issues, tev, days, g) for g in GROUPS},
        "label_history_from_events": have_events,
        "milestones": ms,
        "prs_merged": per_day(pr_merged_ts, days),
        "issues_closed": per_day(issue_closed_ts, days),
        "hero": hero_stats(pr_merged_ts, issue_closed_ts, commit_ts, created, now),
        "speed": {"issue_all": median_hours(is_pairs), "issue_7d": median_hours(is_pairs, week),
                  "issue_24h": median_hours(is_pairs, day),
                  "pr_all": median_hours(pr_pairs), "pr_7d": median_hours(pr_pairs, week),
                  "pr_24h": median_hours(pr_pairs, day)},
    }


def build_snapshot(api, repo, branch, now, rels=_FETCH):
    info = api("/repos/%s" % repo)
    issues = [i for i in _pages(api, "/repos/%s/issues?state=open" % repo)
              if "pull_request" not in i]
    labels = {}
    for i in issues:
        for lb in i.get("labels", []):
            labels[lb["name"]] = labels.get(lb["name"], 0) + 1
    since = (now - timedelta(hours=WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = _pages(api, "/repos/%s/commits?sha=%s&since=%s" % (repo, branch, since))
    by_hour = {}
    for c in commits:
        d = c["commit"]["committer"]["date"][:13]  # YYYY-MM-DDTHH
        by_hour[d] = by_hour.get(d, 0) + 1
    if rels is _FETCH:
        rels = release_downloads(api, repo)
    return {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "downloads": sum(r["downloads"] for r in rels) if rels is not None else None,
        "stars": info.get("stargazers_count", 0),
        "forks": info.get("forks_count", 0),
        "open_issues": len(issues),
        "labels": labels,
        "commits_by_hour": by_hour,
    }


def merge_history(history, snap):
    """Append snap; one snapshot per hour (later wins); keep last KEEP."""
    hour = snap["ts"][:13]
    out = [h for h in history if h.get("ts", "")[:13] != hour] + [snap]
    out.sort(key=lambda h: h["ts"])
    return out[-KEEP:]


# Chart-first page (#1213): all sections are drawn client-side from the embedded series JSON.
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TingLong dashboard</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="icon" href="favicon.ico" sizes="any">
<style>
/* chart-first stats page (#1213): status tiles, donuts, lines, gauges; text second */
:root{color-scheme:light dark;
--bg:light-dark(#f4f6f9,#111418);--card:light-dark(#ffffff,#1a1e24);--ink:light-dark(#11161d,#eef1f4);--mut:light-dark(#5b6470,#a3acb7);
--line:light-dark(#e3e7ec,#2a3038);
--s1:light-dark(#2a78d6,#3987e5);--s2:light-dark(#eb6834,#d95926);--s3:light-dark(#1baf7a,#199e70);--s4:light-dark(#eda100,#c98500);
--s5:light-dark(#e87ba4,#d55181);--s6:light-dark(#008300,#0ca30c);--s7:light-dark(#6250d6,#9085e9);--s8:light-dark(#8a919b,#6f7783);
--good:light-dark(#12804a,#3fbf7f);--bad:light-dark(#cf2f2f,#f06262);--warn:light-dark(#b86e00,#e9a23b)}
*{box-sizing:border-box;min-width:0}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overflow-x:clip}
.w{max-width:1240px;margin:0 auto;padding:20px max(4vw,14px) 48px}
h1{font-size:30px;line-height:1.1;margin:0;font-weight:800}
h2{font-size:22px;margin:36px 0 12px;font-weight:800}
h3{font-size:16px;margin:0 0 8px;font-weight:700}
.top{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 16px}
.mut,.n{color:var(--mut);font-size:13px}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:14px;margin-top:16px}
.card{background:var(--card);border-radius:16px;padding:18px;border:1px solid var(--line)}
.big{font-size:44px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}
.big small{font-size:16px;font-weight:700;margin-left:4px}
.lamp{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:14px}
.lamp i{width:12px;height:12px;border-radius:50%;display:inline-block}
.g2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr));gap:14px}
.g3{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:14px}
.fig{position:relative;width:100%}
.fig svg{display:block;width:100%}
.dn{display:flex;flex-wrap:wrap;align-items:center;gap:14px}
.dn .ring{flex:0 0 180px;max-width:180px}
.lg{flex:1 1 150px;display:flex;flex-direction:column;gap:5px;font-size:14px}
.lg div{display:flex;align-items:center;gap:8px}
.lg i{width:11px;height:11px;border-radius:3px;flex:none}
.lg b{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:700}
.klg{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;margin-top:6px}
.klg i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}
.bar{display:grid;grid-template-columns:110px minmax(0,1fr) 64px;align-items:center;gap:8px;margin:7px 0;font-size:14px}
.bar>span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .tr{position:relative;height:14px;background:var(--line);border-radius:7px}
.bar .tr>div{height:100%;border-radius:7px}
.bar .tk{position:absolute;top:-4px;width:3px;height:22px;background:var(--ink);border-radius:2px}
.bar span:last-child{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chips span{background:var(--line);border-radius:999px;padding:3px 10px;font-size:13px;font-weight:500}
.ms{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;text-align:center;font-size:13px}
select{font:inherit;font-size:15px;padding:6px 10px;min-height:40px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
select:focus-visible,a:focus-visible{outline:3px solid var(--s1);outline-offset:2px}
a{color:var(--s1)}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--card);font-size:13px;padding:6px 10px;border-radius:8px;opacity:0;transition:opacity .1s;z-index:9;white-space:nowrap}
svg text{fill:var(--mut);font:12px system-ui,sans-serif}
@media(max-width:520px){.big{font-size:38px}h1{font-size:24px}.dn .ring{flex-basis:150px}}
</style>
</head>
<body>
<div class="w">
  <div class="top">
    <h1>TingLong dashboard</h1>
    <span class="mut" id="upd"></span>
  </div>
  <p style="margin:6px 0 0"><a href="roadmap.html">Roadmap</a> · <a href="docs.html">Docs</a> · <a id="repo" href="#">GitHub</a></p>

  <div class="hero">
    <div class="card"><h3>PRs merged today</h3><div class="big" id="h_pr"></div><div class="fig" id="sp_pr" style="height:56px;margin-top:10px"></div><div class="mut" id="h_pr2"></div></div>
    <div class="card"><h3>Install to first subtitle</h3><div id="h_ins" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px"></div><div class="mut">Needle past the top = over the 180 s budget</div></div>
    <div class="card"><h3>One release run</h3><div class="big" id="h_rel"></div><div class="fig" id="sp_rel" style="height:56px;margin-top:10px"></div><div class="mut" id="h_rel2"></div></div>
    <div class="card"><h3>Stars</h3><div class="big" id="h_star"></div><div class="fig" id="sp_star" style="height:56px;margin-top:10px"></div><div class="mut" id="h_star2"></div></div>
  </div>

  <h2 id="output">Output</h2>
  <div class="g2">
    <div class="card"><h3>Work finished per day</h3><div class="fig" id="c_thru"></div><div class="klg"><span><i style="background:var(--s1)"></i>PRs merged</span><span><i style="background:var(--s2)"></i>issues closed</span><span><i style="background:var(--s3)"></i>commits</span></div></div>
    <div class="card"><h3>Time from opened to done</h3><div id="b_speed"></div><div class="mut" style="margin-top:8px">Median, last 7 days (hover for 24 h and all time)</div></div>
  </div>

  <h2 id="next">What's next</h2>
  <div class="card"><h3>Milestone progress</h3><div class="ms" id="ms"></div></div>
  <div style="height:14px"></div>
  <div class="g2">
    <div class="card"><h3>Open issues over time</h3><div class="fig" id="c_open"></div></div>
    <div class="card"><h3>Open issues by <select id="dim" aria-label="Group by"></select></h3><div class="dn"><div class="ring" id="d_grp"></div><div class="lg" id="l_grp"></div></div><div class="n" id="lnote"></div></div>
  </div>

  <h2 id="install">Install</h2>
  <div class="card"><h3>Install to first subtitle (seconds, excl. model download, last clean installs)</h3><div class="fig" id="c_ttfs"></div><div class="klg" id="k_ttfs"></div></div>
  <div style="height:14px"></div>
  <div class="g2">
    <div class="card"><h3>Active installs</h3><div id="inst"></div></div>
    <div class="card"><h3>Per-language quality (weekly)</h3><div id="langv"></div><div class="n">CPU mini-eval per pair: bar = MT chrF, colour = gate verdict; hover for ASR error rate and change vs last week.</div></div>
  </div>

  <h2 id="effort">Where the effort goes</h2>
  <div class="g3">
    <div class="card"><h3>By mode</h3><div class="dn"><div class="ring" id="d_mode"></div><div class="lg" id="l_mode"></div></div></div>
    <div class="card"><h3>By platform</h3><div class="dn"><div class="ring" id="d_plat"></div><div class="lg" id="l_plat"></div></div></div>
    <div class="card"><h3>Type mix vs target (black tick)</h3><div id="b_type"></div></div>
  </div>
  <div class="n" id="flow_note" style="margin-top:6px"></div>
  <div class="g3" style="margin-top:14px">
    <div class="card"><h3>Quota minutes per day</h3><div class="fig" id="c_quota"></div></div>
    <div class="card"><h3>PRs merged per 100 quota minutes</h3><div class="fig" id="c_per100"></div></div>
    <div class="card"><h3>Faucet</h3><div id="faucet"></div></div>
  </div>

  <h2 id="releases">Releases</h2>
  <div class="g2">
    <div class="card"><h3>release.yml run (minutes)</h3><div class="fig" id="c_run"></div></div>
    <div class="card"><h3>Release PR open to published (hours)</h3><div class="fig" id="c_prt"></div></div>
  </div>

  <h2 id="popularity">Popularity</h2>
  <div class="g2">
    <div class="card"><h3>Stars and forks</h3><div class="fig" id="c_star"></div><div class="klg"><span><i style="background:var(--s4)"></i>stars</span><span><i style="background:var(--s7)"></i>forks</span></div></div>
    <div class="card"><h3>Downloads <span class="mut" id="dl_tot"></span></h3><div class="fig" id="c_dl"></div><div id="b_dl"></div></div>
  </div>
</div>
<div id="tip" role="tooltip"></div>
<script id="d" type="application/json">__DATA__</script>
<script>
{
const D=JSON.parse(document.getElementById('d').textContent);
const $=id=>document.getElementById(id);
const S=['var(--s1)','var(--s2)','var(--s3)','var(--s4)','var(--s5)','var(--s6)','var(--s7)','var(--s8)'];
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=n=>n==null?'n/a':Math.round(n).toLocaleString('en-US');
const med=a=>{const s=[...a].sort((x,y)=>x-y),m=s.length>>1;return s.length?(s.length%2?s[m]:(s[m-1]+s[m])/2):null};
const nm=k=>k==='(none)'?'unlabelled':String(k).replace(/^infrastructure /,'');
const none=(el,t)=>{el.innerHTML='<p class="n">'+esc(t)+'</p>'};
const days=(D.days||[]).map(d=>d.slice(5).replace('-','/'));

/* tooltip */
const tip=$('tip');
document.addEventListener('mousemove',e=>{const t=e.target.closest&&e.target.closest('[data-t]');
 if(!t){tip.style.opacity=0;return}
 tip.textContent=t.getAttribute('data-t');tip.style.opacity=1;
 tip.style.left=Math.max(4,Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8))+'px';tip.style.top=(e.clientY-36)+'px'});

/* donut */
function donut(el,lg,items,center,sub){
 const tot=items.reduce((a,b)=>a+b.v,0)||1,r=70,C=2*Math.PI*r;let acc=0,g='';
 items.forEach((it,i)=>{const len=C*it.v/tot,gap=items.length>1?2:0,c=it.c||S[i];
  if(len>0)g+=`<circle cx="100" cy="100" r="${r}" fill="none" stroke="${c}" stroke-width="28" stroke-dasharray="${Math.max(len-gap,0.5)} ${C}" stroke-dashoffset="${-acc}" transform="rotate(-90 100 100)" data-t="${esc(it.n)}: ${fmt(it.v)} (${Math.round(100*it.v/tot)}%)"></circle>`;acc+=len});
 el.innerHTML=`<svg viewBox="0 0 200 200" role="img" aria-label="${esc(center+' '+sub)}">${g}<text x="100" y="100" text-anchor="middle" style="font-size:30px;font-weight:800;fill:var(--ink)">${esc(center)}</text><text x="100" y="124" text-anchor="middle" style="font-size:13px">${esc(sub)}</text></svg>`;
 lg.innerHTML=items.map((it,i)=>`<div><i style="background:${it.c||S[i]}"></i>${esc(it.n)}<b>${Math.round(100*it.v/tot)}%</b></div>`).join('')}
function fold(arr,k){const s=[...arr].sort((a,b)=>b.v-a.v);if(s.length<=k)return s;const h=s.slice(0,k-1);h.push({n:'other',v:s.slice(k-1).reduce((a,b)=>a+b.v,0),c:'var(--s8)'});return h}

/* line chart: o = {x, s:[{n,c,v}], h, area, budget, cap, mark, markL, f, tx, label} */
function nice(m){const p=10**Math.floor(Math.log10(m||1)),f=m/p;return (f<=1?1:f<=2?2:f<=5?5:10)*p}
function line(el,o){
 if(!o.s.some(s=>s.v.some(v=>v!=null))){if(!o.h||o.h>=100)none(el,'No data yet.');return}
 const draw=()=>{const H=o.h||220,mini=H<100,W=el.clientWidth||600,L=mini?4:42,R=mini?4:14,T=mini?6:16,B=mini?6:28,n=o.x.length,sm=W<520;
  let mx=0;o.s.forEach(s=>s.v.forEach(v=>{if(v!=null)mx=Math.max(mx,v)}));if(o.budget)mx=Math.max(mx,o.budget*1.2);
  mx=o.cap?Math.min(nice(mx),o.cap):nice(mx);
  const X=i=>L+(W-L-R)*(n<2?.5:i/(n-1)),Y=v=>T+(H-T-B)*(1-Math.min(v,mx)/mx);let g='';
  if(!mini){for(let k=0;k<=2;k++){const v=mx*k/2,y=Y(v);g+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="var(--line)"></line><text x="${L-6}" y="${y+4}" text-anchor="end">${fmt(v)}</text>`}
   const st=Math.max(1,Math.ceil(n/(sm?4:7)));for(let i=0;i<n;i+=st)g+=`<text x="${X(i)}" y="${H-8}" text-anchor="${i===0?'start':i>=n-1?'end':'middle'}">${esc(o.x[i])}</text>`}
  if(o.mark!=null&&o.mark>=0){const x=X(o.mark);g+=`<line class="marker" x1="${x}" x2="${x}" y1="${T}" y2="${H-B}" stroke="var(--mut)" stroke-dasharray="4 4"></line><text x="${x+4}" y="${T+10}">${esc(o.markL)}</text>`}
  if(o.budget){const y=Y(o.budget);g+=`<line class="budget" x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="var(--bad)" stroke-width="2" stroke-dasharray="6 5"></line>`}
  o.s.forEach(s=>{const pts=s.v.map((v,i)=>v==null?null:[X(i),Y(v),v,i]).filter(Boolean);if(!pts.length)return;
   const d=pts.map((p,j)=>(j?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join('');
   if(o.area)g+=`<path d="${d}L${pts[pts.length-1][0]} ${Y(0)}L${pts[0][0]} ${Y(0)}Z" fill="${s.c}" opacity=".14"></path>`;
   g+=`<path d="${d}" fill="none" stroke="${s.c}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"></path>`;
   if(!mini)pts.forEach(p=>{const over=p[2]>mx,lab=o.f?o.f(p[2]):fmt(p[2]);
    g+=`<circle cx="${p[0]}" cy="${p[1]}" r="${over?6:4}" fill="${s.c}" stroke="var(--card)" stroke-width="2"></circle><circle cx="${p[0]}" cy="${p[1]}" r="12" fill="transparent" data-t="${esc((s.n?s.n+' · ':'')+(o.tx?o.tx[p[3]]:o.x[p[3]]))}: ${esc(lab)}"></circle>`;
    if(over)g+=`<text x="${p[0]+9}" y="${p[1]+4}" style="fill:var(--ink);font-weight:700">↑ ${esc(lab)}</text>`});
   else{const p=pts[pts.length-1];g+=`<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${s.c}"></circle>`}});
  el.innerHTML=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.label||'')}">${g}</svg>`};
 draw();if(window.ResizeObserver)new ResizeObserver(draw).observe(el)}

/* gauge: 0..360 s, top = 180 s budget */
function gauge(sec,lbl){if(sec==null)return `<div style="text-align:center" class="n">${esc(lbl)}<br>no data</div>`;
 const f=Math.min(sec/360,1),a=Math.PI*(1-f),x=50+40*Math.cos(a),y=50-40*Math.sin(a),ok=sec<=180,c=ok?'var(--good)':'var(--bad)';
 return `<div style="text-align:center" data-t="${esc(lbl)}: median ${Math.round(sec)} s"><svg viewBox="0 0 100 62" role="img" aria-label="${esc(lbl)} ${Math.round(sec)} s"><path d="M10 50A40 40 0 0 1 90 50" fill="none" stroke="var(--line)" stroke-width="10" stroke-linecap="round"></path><path d="M10 50A40 40 0 0 1 ${x.toFixed(1)} ${y.toFixed(1)}" fill="none" stroke="${c}" stroke-width="10" stroke-linecap="round"></path><line x1="50" y1="4" x2="50" y2="16" stroke="var(--ink)" stroke-width="2"></line></svg><div style="font-weight:800;font-size:20px;line-height:1">${Math.round(sec)}<small style="font-size:12px"> s</small></div><div class="lamp" style="font-size:13px;margin-top:2px"><i style="background:${c};width:9px;height:9px"></i>${esc(lbl)}${ok?'':' !'}</div></div>`}

/* bars */
function bars(el,rows,max){el.innerHTML=rows.map(r=>`<div class="bar" data-t="${esc(r.t||r.n)}"><span>${esc(r.n)}</span><div class="tr"><div style="width:${Math.min(100,100*r.v/(max||1))}%;background:${r.c||'var(--s1)'}"></div>${r.tk!=null?`<i class="tk" style="left:calc(${Math.min(100,100*r.tk/(max||1))}% - 1px)"></i>`:''}</div><span>${esc(r.l)}</span></div>`).join('')}

/* header + hero */
const H0=D.hero||{prs:{},issues:{},commits:{}};
$('upd').textContent=(D.repo||'')+' · '+(D.branch||'')+(H0.day_n?' · day '+H0.day_n:'')+(D.updated?' · updated '+D.updated.slice(0,16).replace('T',' ')+' UTC':'');
if(D.repo)$('repo').href='https://github.com/'+encodeURI(D.repo);
$('h_pr').innerHTML=fmt(H0.prs.today||0)+'<small>PRs</small>';
$('h_pr2').textContent='last 24 h: '+fmt(H0.prs.h24||0)+' PRs, '+fmt(H0.issues.h24||0)+' issues closed'+(H0.prs.h24?' · one PR every '+Math.round(1440/H0.prs.h24)+' min':'');
line($('sp_pr'),{x:days,s:[{c:'var(--s1)',v:D.prs_merged||[]}],h:56,area:1});
const OSN={win32:'Windows',windows:'Windows',linux:'Linux',darwin:'macOS',mac:'macOS',macos:'macOS'},OSC={Windows:'var(--s1)',Linux:'var(--s3)',macOS:'var(--s2)'},ORD=['Windows','Linux','macOS'];
const TT=Object.keys(D.ttfs||{}).map(k=>({k,n:OSN[k]||k,rows:D.ttfs[k]||[]})).sort((a,b)=>{const r=n=>{const i=ORD.indexOf(n);return i<0?9:i};return r(a.n)-r(b.n)});
$('h_ins').innerHTML=TT.length?TT.map(o=>gauge(med(o.rows.map(r=>r.seconds)),o.n)).join(''):'<div class="n" style="grid-column:1/-1">No install runs yet.</div>';
const rel=(D.release_times||[]).filter(r=>r.run_min!=null).reverse();
if(rel.length){$('h_rel').innerHTML=Math.round(rel[rel.length-1].run_min)+'<small>min</small>';
 $('h_rel2').textContent=rel[rel.length-1].tag+' · slowest of the last '+rel.length+': '+Math.round(Math.max(...rel.map(r=>r.run_min)))+' min';
 line($('sp_rel'),{x:rel.map(r=>r.tag),s:[{c:'var(--s3)',v:rel.map(r=>r.run_min)}],h:56,area:1})}
else $('h_rel').innerHTML='<small>no releases yet</small>';
const T0=D.totals||{};
$('h_star').innerHTML=fmt(T0.stars||0)+'<small>stars</small>';
$('h_star2').textContent='forks '+fmt(T0.forks||0)+((D.downloads||{}).total!=null?' · downloads '+fmt(D.downloads.total):'');
line($('sp_star'),{x:days,s:[{c:'var(--s4)',v:D.stars||[]}],h:56,area:1});

/* Output */
line($('c_thru'),{x:days,s:[{n:'PRs merged',c:'var(--s1)',v:D.prs_merged||[]},{n:'issues closed',c:'var(--s2)',v:D.issues_closed||[]},{n:'commits',c:'var(--s3)',v:D.commits||[]}],label:'Work finished per day'});
const fh=h=>h==null?'n/a':h<1?Math.round(h*60)+' min':h<48?h.toFixed(1)+' h':(h/24).toFixed(1)+' days';
const Sp=D.speed||{},smx=Math.max(Sp.issue_7d||0,Sp.pr_7d||0)||1;
bars($('b_speed'),[['issue','Issue opened to closed','var(--s2)'],['pr','PR opened to merged','var(--s1)']].map(([k,l,c])=>({n:k==='pr'?'PR':'issue',v:Sp[k+'_7d']||0,l:fh(Sp[k+'_7d']),c,t:l+': 7 days '+fh(Sp[k+'_7d'])+' · 24 h '+fh(Sp[k+'_24h'])+' · all time '+fh(Sp[k+'_all'])})),smx);

/* What's next */
const mr=p=>{const r=30,C=2*Math.PI*r,c=p>=100?'var(--good)':'var(--s1)';return `<svg viewBox="0 0 80 80" width="80" height="80" style="display:block;margin:0 auto"><circle cx="40" cy="40" r="${r}" fill="none" stroke="var(--line)" stroke-width="9"></circle><circle cx="40" cy="40" r="${r}" fill="none" stroke="${c}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${C*p/100} ${C}" transform="rotate(-90 40 40)"></circle><text x="40" y="45" text-anchor="middle" style="font-size:16px;font-weight:800;fill:var(--ink)">${p}%</text></svg>`};
const MS=(D.milestones||[]).filter(m=>m.open+m.closed>0).sort((a,b)=>(a.closed/(a.open+a.closed))-(b.closed/(b.open+b.closed)));
$('ms').innerHTML=MS.map(m=>{const p=Math.round(100*m.closed/(m.open+m.closed));
 return `<div data-t="${esc(m.title)}: ${m.closed} closed, ${m.open} open">${mr(p)}<b>${esc(m.title)}</b><div class="mut">${m.open} open</div></div>`}).join('')||'<p class="n">No milestones.</p>';
line($('c_open'),{x:days,s:[{c:'var(--s2)',v:D.open_issues||[]}],area:1,label:'Open issues'});
const dim=$('dim'),G=D.groups||{};
Object.keys(G).forEach(g=>dim.add(new Option(g==='priority'?'priority (P0-P4)':g,g)));
const showG=()=>{const o=G[dim.value]||{},n=(D.days||[]).length;
 const it=Object.keys(o).map(k=>({n:nm(k),v:o[k][n-1]||0})).filter(x=>x.v>0);
 $('lnote').textContent=D.label_history_from_events===false?'Label events unavailable: current labels only.':'';
 if(!it.length){none($('d_grp'),'No open issues.');$('l_grp').innerHTML='';return}
 donut($('d_grp'),$('l_grp'),fold(it,7),fmt(T0.open!=null?T0.open:it.reduce((a,b)=>a+b.v,0)),'open')};
dim.onchange=showG;if(G.status)dim.value='status';showG();

/* Install */
line($('c_ttfs'),(()=>{const n=Math.max(0,...TT.map(o=>o.rows.length)),first=TT.find(o=>o.rows.length);
 return {x:Array.from({length:n},(_,i)=>'run '+(i+1)),tx:Array.from({length:n},(_,i)=>{const r=TT.map(o=>o.rows[i]).find(Boolean);return r?'v'+r.version+' '+r.date.slice(5):''}),
 s:TT.map(o=>({n:o.n,c:OSC[o.n]||'var(--s7)',v:o.rows.map(r=>r.seconds)})),budget:first?(D.ttfs_budget_s||180):0,cap:400,h:260,f:v=>Math.round(v)+' s',label:'Install to first subtitle'}})());
$('k_ttfs').innerHTML=TT.map(o=>`<span><i style="background:${OSC[o.n]||'var(--s7)'}"></i>${esc(o.n)}</span>`).join('')+(TT.length?'<span><i style="background:var(--bad)"></i>180 s budget</span>':'');
const I=D.installs;
if(I&&I.days&&I.days.length){const last=I.total[I.total.length-1];
 $('inst').innerHTML=`<div class="big">${fmt(last)}<small>yesterday</small></div><div class="fig" id="c_inst" style="margin-top:8px"></div><div id="b_inst"></div><div class="n">Anonymous update checks (date, version, OS only; at most once a day per install), so checks per day ~ active installs.</div>`;
 line($('c_inst'),{x:I.days.map(d=>d.slice(5).replace('-','/')),s:[{c:'var(--s3)',v:I.total}],area:1,h:160,label:'Active installs per day'});
 const vm=Math.max(1,...I.versions.map(v=>v.week_avg));
 bars($('b_inst'),I.versions.slice(0,6).map(v=>({n:v.version+' '+v.os,v:v.week_avg,l:v.week_avg.toFixed(1)+'/day',c:'var(--s3)',t:v.version+' '+v.os+': last day '+v.last_day+', 7-day avg '+v.week_avg.toFixed(1)})),vm)}
else none($('inst'),'Install counter not available yet (cloudflare/update-counter).');
const LV=D.lang_verdicts;
if(LV&&LV.rows&&LV.rows.length){const f2=(v,s)=>v==null?'n/a':(s&&v>0?'+':'')+v.toFixed(2);
 bars($('langv'),LV.rows.map(r=>({n:r.language,v:r.mt||0,l:r.verdict,c:r.verdict==='PASS'?'var(--good)':'var(--bad)',t:r.language+': chrF '+f2(r.mt)+' ('+f2(r.d_mt,1)+'), ASR '+f2(r.asr)+'% ('+f2(r.d_asr,1)+') · '+LV.date+(LV.previous_date?' vs '+LV.previous_date:'')})),100)}
else none($('langv'),'no data yet');

/* Where the effort goes */
const F=D.flow||{total:0,modes:[],platforms:[],type_mix:[]};
if(F.total>0){
 donut($('d_mode'),$('l_mode'),fold(F.modes.map(m=>({n:nm(m.name),v:m.quota})),7),fmt(F.total),'quota min');
 donut($('d_plat'),$('l_plat'),fold(F.platforms.map(m=>({n:m.name==='all'?'all platforms':m.name,v:m.quota})),5),fmt(F.total),'quota min');
 bars($('b_type'),F.type_mix.map(t=>({n:t.type,v:t.share,tk:t.target,l:Math.round(t.share)+'%',c:Math.abs(t.delta)>5?'var(--warn)':'var(--s1)',t:t.type+': '+t.share.toFixed(1)+'% of quota, target '+t.target+'%'})),50);
 const inf=F.inferred||{};$('flow_note').textContent='Quota minutes = minutes x model weight (opus = 2x sonnet), PRs merged in the last 48 h. Inferred from changed files: platform '+Math.round(inf.os||0)+'%, language '+Math.round(inf.lang||0)+'%.';
}else{['d_mode','d_plat','b_type'].forEach(id=>none($(id),'No quota minutes in the last 48 h.'))}
const BF=F.backfill;
if(BF&&BF.total>0){const bi=BF.inferred||{};$('flow_note').textContent+=(F.total>0?' ':'')+'Last '+BF.days+' days: '+fmt(BF.total)+' quota min; inferred mode '+Math.round(bi.mode||0)+'%, platform '+Math.round(bi.os||0)+'%, language '+Math.round(bi.lang||0)+'%; unclassified mode '+Math.round(BF.unclassified||0)+'%.'}
const Q=D.quota_trend;
if(Q&&Q.days){const qs=Math.max(0,Q.quota.findIndex(v=>v>0)-1),qd=Q.days.slice(qs).map(d=>d.slice(5).replace('-','/')),mk=Q.days.slice(qs).indexOf(Q.marker);
 line($('c_quota'),{x:qd,s:[{c:'var(--s1)',v:Q.quota.slice(qs)}],area:1,mark:mk,markL:Q.marker_pr?'#'+Q.marker_pr:'',label:'Quota minutes per day'});
 line($('c_per100'),{x:qd,s:[{c:'var(--s2)',v:Q.per100.slice(qs)}],mark:mk,markL:Q.marker_pr?'#'+Q.marker_pr:'',f:v=>v.toFixed(1),label:'PRs per 100 quota minutes'})}
else{none($('c_quota'),'No data yet.');none($('c_per100'),'No data yet.')}
const FZ=D.faucet;
$('faucet').innerHTML=FZ?`<div class="big">${fmt(FZ.level)}<small>level</small></div><h3 style="margin-top:14px">Current focus</h3><div class="chips">${(FZ.focus||[]).map(f=>`<span>${esc(f)}</span>`).join('')||'<span>none</span>'}</div>`:'<p class="n">Faucet level unavailable.</p>';

/* Releases */
line($('c_run'),{x:rel.map(r=>r.tag),s:[{c:'var(--s3)',v:rel.map(r=>r.run_min)}],area:1,f:v=>Math.round(v)+' min',label:'release.yml run minutes'});
line($('c_prt'),{x:rel.map(r=>r.tag),s:[{c:'var(--s7)',v:rel.map(r=>r.pr_min==null?null:r.pr_min/60)}],area:1,f:v=>v.toFixed(1)+' h',label:'Release PR open to published'});

/* Popularity */
line($('c_star'),{x:days,s:[{n:'stars',c:'var(--s4)',v:D.stars||[]},{n:'forks',c:'var(--s7)',v:D.forks||[]}],label:'Stars and forks'});
const DL=D.downloads||{releases:[],series:[]};
$('dl_tot').textContent=DL.total==null?'':'total '+fmt(DL.total);
if((DL.series||[]).some(v=>v!=null))line($('c_dl'),{x:days,s:[{c:'var(--s3)',v:DL.series}],area:1,h:140,label:'Downloads over time'});
const dl=(DL.releases||[]).filter(r=>r.downloads>0).sort((a,b)=>b.downloads-a.downloads).slice(0,10);
if(dl.length)bars($('b_dl'),dl.map(r=>({n:r.tag,v:r.downloads,l:fmt(r.downloads),c:'var(--s3)',t:r.tag+': '+fmt(r.downloads)+' downloads'})),Math.max(...dl.map(r=>r.downloads)));
else none($('b_dl'),'No release downloads yet.');
}
</script>
</body>
</html>
"""


def render_html(series):
    data = dict(series, ttfs_budget_s=TTFS_BUDGET_S)
    # "<" -> \u003c keeps "</script>" and "<!--" in labels from ending or confusing the data block
    payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    return PAGE.replace("__DATA__", payload, 1)


def run(outdir, repo, branch, api=http_api, now=None, installs=_FETCH):
    now = now or datetime.now(UTC)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "data.json")
    try:
        with open(path, encoding="utf-8") as f:
            history = json.load(f)
        if not isinstance(history, list):
            history = []
    except (OSError, ValueError):
        history = []
    rels = release_downloads(api, repo)  # one fetch per run, shared by snapshot and series
    history = merge_history(history, build_snapshot(api, repo, branch, now, rels))
    files_path = os.path.join(outdir, PR_FILES)
    try:
        with open(files_path, encoding="utf-8") as f:
            files_cache = json.load(f)
        if not isinstance(files_cache, dict):
            files_cache = {}
    except (OSError, ValueError):
        files_cache = {}
    series = build_series(api, repo, branch, now, history, rels, files_cache)
    if installs is _FETCH:
        installs = fetch_installs(counter_base())
    try:  # never let a bad counter answer stop the dashboard build
        series["installs"] = active_installs(valid_install_rows(installs) if installs is not None else None, now)
    except Exception as e:
        print("install counter unavailable: %s" % e, file=sys.stderr)
        series["installs"] = None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f)
    with open(files_path, "w", encoding="utf-8") as f:
        json.dump(prune_files_cache(files_cache), f)
    with open(os.path.join(outdir, "series.json"), "w", encoding="utf-8") as f:
        json.dump(series, f)
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(series))
    return history


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="dev")
    a = ap.parse_args(argv)
    h = run(a.outdir, a.repo, a.branch)
    print("snapshots: %d" % len(h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
