#!/usr/bin/env python3
"""Hourly stats dashboard (stdlib only, GITHUB_TOKEN only, no telemetry).

Usage: python scripts/stats_dashboard.py OUTDIR --repo OWNER/NAME [--branch dev]

Reads OUTDIR/data.json (previous snapshots, optional), appends one snapshot and
writes OUTDIR/data.json + OUTDIR/index.html (self-contained, inline SVG charts).
"""
import argparse
import html
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

KEEP = 24 * 90  # hourly snapshots kept (~90 days)
WINDOW_H = 72


def http_api(path):
    """GET https://api.github.com<path> -> parsed JSON (uses GITHUB_TOKEN if set)."""
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Accept", "application/vnd.github+json")
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _pages(api, path, limit=20):
    sep = "&" if "?" in path else "?"
    out = []
    for page in range(1, limit + 1):
        chunk = api("%s%sper_page=100&page=%d" % (path, sep, page))
        out.extend(chunk)
        if len(chunk) < 100:
            break
    return out


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


def _line(points, w=600, h=140, color="#2b7"):
    if not points:
        return "<p>no data</p>"
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1
    n = max(len(points) - 1, 1)
    pts = " ".join("%.1f,%.1f" % (i * w / n, h - 10 - (v - lo) * (h - 20) / span)
                   for i, v in enumerate(points))
    return ('<svg viewBox="0 0 %d %d" width="100%%"><polyline fill="none" stroke="%s" '
            'stroke-width="2" points="%s"/><text x="2" y="12" font-size="11">max %s</text>'
            '<text x="2" y="%d" font-size="11">min %s</text></svg>'
            % (w, h, color, pts, hi, h - 2, lo))


def _bars(items, w=600, bar=16):
    if not items:
        return "<p>no data</p>"
    top = max(v for _, v in items) or 1
    rows = []
    for i, (k, v) in enumerate(items):
        bw = v * (w - 220) / top
        y = i * (bar + 4)
        rows.append('<text x="0" y="%d" font-size="12">%s</text>'
                    '<rect x="200" y="%d" width="%.1f" height="%d" fill="#37a"/>'
                    '<text x="%.1f" y="%d" font-size="12">%d</text>'
                    % (y + 12, html.escape(k), y, bw, bar, 205 + bw, y + 12, v))
    return '<svg viewBox="0 0 %d %d" width="100%%">%s</svg>' % (w, len(items) * (bar + 4), "".join(rows))


def render_html(history, repo, branch):
    last = history[-1]
    tiles = "".join('<div class="t"><b>%s</b><span>%s</span></div>' % (v, k) for k, v in (
        ("stars", last["stars"]), ("forks", last["forks"]), ("open issues", last["open_issues"]),
        ("commits 72h", sum(last["commits_by_hour"].values()))))
    hours = sorted(last["commits_by_hour"].items())
    days = {}
    for k, v in hours:
        days[k[:10]] = days.get(k[:10], 0) + v
    labels = sorted(last["labels"].items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    return """<!doctype html><meta charset="utf-8"><title>tlwu stats</title>
<style>body{font:14px sans-serif;max-width:680px;margin:1em auto;padding:0 1em}
.t{display:inline-block;margin:4px;padding:8px 14px;border:1px solid #ccc;border-radius:6px}
.t b{display:block;font-size:22px}</style>
<h1>%s stats (%s)</h1><p>Updated %s UTC. Last 72 h shown for commits.</p>%s
<h2>Commits per hour (last 72 h)</h2>%s<h2>Commits per day</h2>%s
<h2>Stars over time</h2>%s<h2>Forks over time</h2>%s<h2>Open issues over time</h2>%s
<h2>Open issues by label</h2>%s""" % (
        html.escape(repo), html.escape(branch), last["ts"], tiles,
        _line([v for _, v in hours]), _bars(sorted(days.items())),
        _line([h["stars"] for h in history]), _line([h["forks"] for h in history]),
        _line([h["open_issues"] for h in history]), _bars(labels))


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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f)
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(history, repo, branch))
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
