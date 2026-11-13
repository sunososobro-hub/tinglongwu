#!/usr/bin/env python3
"""Hourly stats dashboard (stdlib only, GITHUB_TOKEN only, no telemetry).

Usage: python scripts/stats_dashboard.py OUTDIR --repo OWNER/NAME [--branch dev]

Reads OUTDIR/data.json (hourly snapshots, optional), appends one snapshot, rebuilds the
full-history series from the API (series.json) and writes a self-contained index.html.
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

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
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


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
UNLABELLED = "unlabelled"
FOCUS_ORANGE = "#e08a1e"


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


def _label_value(labels, prefix):
    for name in labels:
        if name.startswith(prefix):
            return name[len(prefix):]
    return UNLABELLED


def build_flow(items):
    """items: [{'model','minutes','labels':[names]}] -> quota-flow data.

    Everything is in quota minutes (minutes x model weight, opus = 2x). Missing mode / os /
    lang labels go to an 'unlabelled' node instead of failing.
    """
    paths, types = {}, {}
    for it in items:
        q = it["minutes"] * QUOTA_WEIGHT.get(it["model"], 1.0)
        if q <= 0:
            continue
        labels = it.get("labels", [])
        key = (_label_value(labels, "mode:"), _label_value(labels, "os:"), _label_value(labels, "lang:"))
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
            "type_mix": mix, "side": [{"type": n, "target": t} for n, t in SIDE_TARGETS]}


def render_sankey(flow, focus=()):
    """Inline-SVG Sankey: quota -> mode -> platform -> language. Focus values are orange."""
    total = flow["total"]
    if not total:
        return '<p class="n">No quota minutes in the last %d h.</p>' % FLOW_HOURS
    cols = [[{"name": "quota", "quota": total}], flow["modes"], flow["platforms"], flow["langs"]]
    W, H, NW, GAP, PAD = 640, 220, 10, 4, 6
    xs = [PAD + i * (W - 2 * PAD - NW) / 3 for i in range(4)]
    scale = (H - 2 * PAD - GAP * max(len(c) for c in cols)) / total
    pos = []  # per column: name -> [y, height]
    for col in cols:
        y, d = PAD, {}
        for n in col:
            h = max(n["quota"] * scale, 1.0)
            d[n["name"]] = [y, h]
            y += h + GAP
        pos.append(d)
    focus = {f.lower() for f in focus}
    out = []
    for c in range(3):
        links = {}
        for p in flow["paths"]:
            a, b = ("quota" if c == 0 else p[c - 1]), p[c]
            links[(a, b)] = links.get((a, b), 0.0) + p[3]
        order_a = list(pos[c])
        order_b = list(pos[c + 1])
        off_a = {n: pos[c][n][0] for n in order_a}
        off_b = {n: pos[c + 1][n][0] for n in order_b}
        for (a, b), v in sorted(links.items(), key=lambda kv: (order_a.index(kv[0][0]), order_b.index(kv[0][1]))):
            h = v * scale
            x1, x2 = xs[c] + NW, xs[c + 1]
            y1, y2 = off_a[a] + h / 2, off_b[b] + h / 2
            off_a[a] += h
            off_b[b] += h
            col = FOCUS_ORANGE if b.lower() in focus else "#3b82c4"
            out.append('<path d="M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f" fill="none" stroke="%s" '
                       'stroke-opacity=".35" stroke-width="%.2f"><title>%s \u2192 %s: %.1f%% of quota</title></path>'
                       % (x1, y1, (x1 + x2) / 2, y1, (x1 + x2) / 2, y2, x2, y2, col, max(h, 0.5),
                          html.escape(a), html.escape(b), 100.0 * v / total))
    for c, col in enumerate(cols):
        for n in col:
            y, h = pos[c][n["name"]]
            fill = FOCUS_ORANGE if n["name"].lower() in focus else "#667"
            label = n["name"] if c == 0 else "%s %.0f%%" % (n["name"], 100.0 * n["quota"] / total)
            tx, anchor = (xs[c] + NW + 3, "start") if c < 3 else (xs[c] - 3, "end")
            out.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" fill="%s"/><text x="%.1f" y="%.1f" '
                       'text-anchor="%s">%s</text>' % (xs[c], y, NW, h, fill, tx, y + h / 2 + 4, anchor,
                                                      html.escape(label)))
    return '<svg viewBox="0 0 %d %d" role="img" aria-label="quota flow">%s</svg>' % (W, H, "".join(out))


def render_type_mix(flow):
    rows = []
    for r in flow["type_mix"]:
        rows.append('<div style="display:flex;align-items:center;gap:8px;margin:3px 0"><span style="width:96px">%s</span>'
                    '<div class="pb" style="flex:1;position:relative"><div style="width:%.1f%%"></div>'
                    '<i style="position:absolute;left:%d%%;top:-2px;width:2px;height:14px;background:var(--fg)"></i></div>'
                    '<span style="width:120px">%.1f%% / target %d</span></div>'
                    % (html.escape(r["type"]), min(r["share"], 100.0), min(r["target"], 100), r["share"], r["target"]))
    side = "".join('<span class="t" style="padding:4px 10px">%s <b style="font-size:14px;display:inline">%d%%</b> '
                   '<small>fixed schedule</small></span>' % (html.escape(s["type"]), s["target"]) for s in flow["side"])
    return "".join(rows) + "<div>" + side + "</div>"


def render_faucet(faucet):
    if not faucet:
        return '<p class="n">Faucet level unavailable.</p>'
    focus = ", ".join(html.escape(f) for f in faucet["focus"]) or "none"
    return ('<p>Faucet level <b>%d</b> &middot; focus <b style="color:%s">%s</b></p>'
            % (faucet["level"], FOCUS_ORANGE, focus))


def render_flow_section(series):
    flow = series.get("flow")
    if not flow:
        return ""
    faucet = series.get("faucet")
    focus = faucet["focus"] if faucet else ()
    return ('<h2>Where the quota goes</h2>%s'
            '<p class="n">Quota minutes (minutes x model weight, opus = 2x sonnet), PRs merged in the last %d h; '
            'orange = current focus.</p>'
            '<h2>Type mix vs target</h2>%s'
            '<p class="n">Share of quota per issue type; the tick marks the target.</p>'
            '<h2>Faucet</h2>%s<p class="n">Throttle level and focus read from issue #574.</p>'
            % (render_sankey(flow, focus), FLOW_HOURS, render_type_mix(flow), render_faucet(faucet)))


def build_flow_inputs(api, repo, merged_prs, now):
    """Quota flow from recent merged PRs' usage lines + linked-issue labels, and the #574 faucet."""
    since = now - timedelta(hours=FLOW_HOURS)
    items, cache = [], {}
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
        items.append({"model": usage[0], "minutes": usage[1], "labels": cache.get(n, [])})
    try:
        faucet = parse_faucet(api("/repos/%s/issues/574" % repo).get("body"))
    except Exception as e:
        print("faucet unavailable: %s" % e, file=sys.stderr)
        faucet = None
    return build_flow(items), faucet


def build_series(api, repo, branch, now):
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
    flow, faucet = build_flow_inputs(api, repo, merged_prs, now)
    return {
        "flow": flow, "faucet": faucet,
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


def build_snapshot(api, repo, branch, now):
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
    return {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
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


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>tlwu stats</title><link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="icon" href="favicon.ico" sizes="any">
<style>
:root{--bg:#fff;--fg:#1d2329;--mut:#667;--bd:#d0d7de;--a:#2a7d4f;--grid:#e6e9ec}
@media (prefers-color-scheme:dark){:root{--bg:#14181c;--fg:#e4e8ec;--mut:#9aa4ae;--bd:#30363d;--a:#4cc38a;--grid:#262c33}}
body{font:14px/1.4 system-ui,sans-serif;max-width:760px;margin:0 auto;padding:12px 16px;background:var(--bg);color:var(--fg)}
.t{display:inline-block;margin:4px 4px 4px 0;padding:8px 14px;border:1px solid var(--bd);border-radius:6px}
.t b{display:block;font-size:22px}h2{font-size:16px;margin:22px 0 4px}
svg{width:100%;height:auto;display:block}svg text{fill:var(--mut);font-size:11px}
select{font:inherit;padding:4px 8px;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:4px}
.lg{display:flex;flex-wrap:wrap;gap:4px 12px;margin:6px 0;font-size:12px}
.lg i{display:inline-block;width:10px;height:10px;margin-right:4px;border-radius:2px}
.pb{height:10px;background:var(--grid);border-radius:5px;overflow:hidden}.pb div{height:100%;background:var(--a)}
small,p.n{color:var(--mut)}
</style></head><body>
<h1 style="font-size:20px">__REPO__ progress</h1>
<p style="font-size:16px;margin:4px 0 12px"><a href="roadmap.html">&#x1F5FA;&#xFE0F; Roadmap</a> &middot; <a href="docs.html">Docs</a></p>
<p class="n" id="upd"></p><p id="sum" style="font-size:16px"></p>
<div id="hero"></div>
__FLOW__
<h2>Work finished per day</h2><div id="c_thru"></div>
<div class="lg"><span><i style="background:#3b82c4"></i>PRs merged</span><span><i style="background:#e08a1e"></i>issues closed</span></div>
<h2>Commits per day</h2><div id="c_commits"></div>
<h2>How fast things get done</h2><div id="speed"></div>
<h2>Milestone progress</h2><div id="ms"></div>
<h2>Open issues over time</h2><div id="c_open"></div>
<h2>Open issues by <select id="dim"></select></h2>
<p class="n" id="lnote"></p><div id="c_grp"></div><div class="lg" id="lg"></div>
<div id="cur"></div>
<h2>Stars</h2><div id="c_stars"></div>
<h2>Forks</h2><div id="c_forks"></div>
<script id="d" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('d').textContent);
const W=640,H=170,L=34,B=20,T=8;
const PAL=['#3b82c4','#e08a1e','#2a9d6f','#c2410c','#8b5cf6','#d1477a','#0e9aa7','#8a7f1f','#6b7280','#a16207'];
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function axes(max,n){let s='';for(let k=0;k<=2;k++){const y=T+(H-T-B)*(1-k/2);
 s+=`<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" stroke="var(--grid)"/><text x="${L-4}" y="${y+4}" text-anchor="end">${Math.round(max*k/2)}</text>`}
 const step=Math.max(1,Math.ceil(n/6));
 for(let i=0;i<n;i+=step){const x=L+(W-L)*(n<2?0:i/(n-1));
  s+=`<text x="${Math.min(x,W-18)}" y="${H-4}" text-anchor="${i?'middle':'start'}">${D.days[i].slice(5)}</text>`}
 return s}
function X(i,n,bar){return bar?L+(W-L)*(i+.5)/n:L+(W-L)*(n<2?0:i/(n-1))}
function chart(el,series,opt){
 const n=D.days.length;let max=1;
 if(opt.stack){for(let i=0;i<n;i++)max=Math.max(max,series.reduce((a,s)=>a+s.v[i],0))}
 else series.forEach(s=>s.v.forEach(v=>max=Math.max(max,v)));
 let g=axes(max,n);const Y=v=>T+(H-T-B)*(1-v/max);
 if(opt.stack){const bw=(W-L)/n*.8;
  for(let i=0;i<n;i++){let acc=0;series.forEach((s,k)=>{const v=s.v[i];if(!v)return;
   g+=`<rect x="${X(i,n,1)-bw/2}" y="${Y(acc+v)}" width="${bw}" height="${Y(acc)-Y(acc+v)}" fill="${opt.pal?opt.pal[k]:PAL[k%PAL.length]}"><title>${D.days[i]} ${esc(s.name)}: ${v}</title></rect>`;acc+=v})}}
 else if(opt.bar){const bw=(W-L)/n*.8;series[0].v.forEach((v,i)=>{g+=`<rect x="${X(i,n,1)-bw/2}" y="${Y(v)}" width="${bw}" height="${Y(0)-Y(v)}" fill="${opt.color}"><title>${D.days[i]}: ${v}</title></rect>`})}
 else series.forEach(s=>{g+=`<polyline fill="none" stroke="${opt.color}" stroke-width="2" points="${s.v.map((v,i)=>X(i,n)+','+Y(v)).join(' ')}"/>`});
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}">${g}</svg>`}
document.getElementById('upd').textContent='Updated '+D.updated+' UTC. Data from repo creation ('+D.created.slice(0,10)+') to today.';
const Hh=D.hero;
const big=(v,k)=>`<div class="t"><b>${v}</b><span>${k}</span></div>`;
const row=(title,f)=>`<p class="n" style="margin:10px 0 0">${title}</p>`+[['prs','PRs merged'],['issues','issues closed'],['commits','commits']].map(([k,l])=>big(Hh[k][f],l)).join('');
document.getElementById('hero').innerHTML=row('Today (UTC)','today')+row('Last 24 hours','h24')+row('Since day one (day '+Hh.day_n+')','total');
const per=Hh.prs.h24?Math.round(1440/Hh.prs.h24):0;
document.getElementById('sum').textContent='Day '+Hh.day_n+' \u00b7 '+Hh.prs.h24+' PRs merged in the last 24 h'+(per?' \u00b7 1 PR every '+per+' min':'');
const fh=h=>h==null?'n/a':h<1?Math.round(h*60)+' min':h<48?h.toFixed(1)+' h':(h/24).toFixed(1)+' days';
const Sp=D.speed;
document.getElementById('speed').innerHTML='<table><tr><th></th><th>Last 7 days</th><th>Last 24 h</th><th>All time</th></tr>'+[['issue','Issue opened to closed'],['pr','PR opened to merged']].map(([k,l])=>'<tr><td>'+l+'</td><td>'+fh(Sp[k+'_7d'])+'</td><td>'+fh(Sp[k+'_24h'])+'</td><td>'+fh(Sp[k+'_all'])+'</td></tr>').join('')+'</table><p class="n">Typical (median) time from opened to done.</p>';
chart(c_thru,[{name:'PRs merged',v:D.prs_merged},{name:'issues closed',v:D.issues_closed}],{stack:1,pal:['#3b82c4','#e08a1e']});
chart(c_commits,[{name:'commits',v:D.commits}],{bar:1,color:'var(--a)'});
chart(c_stars,[{name:'stars',v:D.stars}],{color:'#e0a81e'});
chart(c_forks,[{name:'forks',v:D.forks}],{color:'#3b82c4'});
chart(c_open,[{name:'open',v:D.open_issues}],{color:'#c2410c'});
const dim=document.getElementById('dim');
Object.keys(D.groups).forEach(g=>dim.add(new Option(g==='priority'?'priority (P0-P4)':g,g)));
function showGroup(){const g=dim.value,o=D.groups[g],n=D.days.length;
 const names=Object.keys(o).sort((a,b)=>o[b][n-1]-o[a][n-1]||a.localeCompare(b));
 const ser=names.map(k=>({name:k,v:o[k]}));
 document.getElementById('lnote').textContent=D.label_history_from_events?'History reconstructed from issue label/milestone events (an issue with several labels in this group counts once per label).':'Label events unavailable: history shows current labels only.';
 chart(c_grp,ser,{stack:1});
 lg.innerHTML=names.map((k,i)=>`<span><i style="background:${PAL[i%PAL.length]}"></i>${esc(k)}</span>`).join('');
 cur.innerHTML='<p><b>Now:</b> '+(names.map(k=>`${esc(k)} <b>${o[k][n-1]}</b>`).join(' &middot; ')||'none')+'</p>'}
dim.onchange=showGroup;
ms.innerHTML=D.milestones.map(m=>{const t=m.open+m.closed,p=t?Math.round(100*m.closed/t):0;
 return `<p><b>${esc(m.title)}</b> <small>${m.closed} closed / ${m.open} open (${p}%)</small></p><div class="pb"><div style="width:${p}%"></div></div>`}).join('')||'<p>none</p>';
dim.value='status';showGroup();
</script></body></html>"""


def render_html(series):
    data = json.dumps(series, separators=(",", ":")).replace("</", "<\\/")
    values = {"__REPO__": html.escape(series["repo"]), "__BRANCH__": html.escape(series["branch"]),
              "__FLOW__": render_flow_section(series), "__DATA__": data}
    # one pass over the template: inserted content (labels, JSON) is never scanned for placeholders
    return re.sub("|".join(values), lambda m: values[m.group(0)], PAGE)


def run(outdir, repo, branch, api=http_api, now=None):
    now = now or datetime.now(timezone.utc)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "data.json")
    try:
        with open(path, encoding="utf-8") as f:
            history = json.load(f)
        if not isinstance(history, list):
            history = []
    except (OSError, ValueError):
        history = []
    history = merge_history(history, build_snapshot(api, repo, branch, now))
    series = build_series(api, repo, branch, now)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f)
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
