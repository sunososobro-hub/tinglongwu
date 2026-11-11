#!/usr/bin/env python3
"""Static stats dashboard for the repo (stdlib only, GITHUB_TOKEN only).

Usage: python scripts/stats_dashboard.py OUT_DIR [--repo owner/name] [--branch dev]
Token from GITHUB_TOKEN / GH_TOKEN env; falls back to `gh auth token`.
Writes OUT_DIR/index.html and OUT_DIR/data.json (history of snapshots kept
in data.json["snapshots"] when a previous data.json is present in OUT_DIR).
"""
import argparse, json, os, subprocess, sys, urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"
API = "https://api.github.com"


def token():
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        return ""


def get(url, tok, accept="application/vnd.github+json"):
    h = {"Accept": accept, "User-Agent": "tlwu-stats-dashboard",
         "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
        return json.load(r)


def pages(path, tok, accept="application/vnd.github+json", cap=200):
    out = []
    for p in range(1, cap + 1):
        sep = "&" if "?" in path else "?"
        chunk = get(f"{API}{path}{sep}per_page=100&page={p}", tok, accept)
        out += chunk
        if len(chunk) < 100:
            break
    return out


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def collect(repo, branch, tok):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    info = get(f"{API}/repos/{repo}", tok)
    commits = pages(f"/repos/{repo}/commits?sha={branch}", tok)
    cts = [parse(c["commit"]["committer"]["date"]) for c in commits]
    per_day = Counter(t.strftime("%Y-%m-%d") for t in cts)
    per_hour = Counter(t.strftime("%Y-%m-%dT%H:00Z") for t in cts)
    # fill gaps: all days since first commit; hourly for last 72h
    days = []
    if cts:
        d = min(cts).date()
        while d <= now.date():
            k = d.isoformat()
            days.append([k, per_day.get(k, 0)])
            d += timedelta(days=1)
    hours = []
    h0 = now.replace(minute=0, second=0) - timedelta(hours=71)
    for i in range(72):
        k = (h0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00Z")
        hours.append([k, per_hour.get(k, 0)])
    stars = pages(f"/repos/{repo}/stargazers", tok, "application/vnd.github.star+json")
    sts = sorted(parse(s["starred_at"]) for s in stars)
    star_series = [[t.strftime("%Y-%m-%dT%H:%M:%SZ"), i + 1] for i, t in enumerate(sts)]
    issues = [i for i in pages(f"/repos/{repo}/issues?state=open", tok) if "pull_request" not in i]
    labels = Counter()
    for i in issues:
        if not i["labels"]:
            labels["(no label)"] += 1
        for l in i["labels"]:
            labels[l["name"]] += 1
    return {
        "repo": repo, "branch": branch,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stars": info["stargazers_count"], "forks": info["forks_count"],
        "open_issues": len(issues),
        "total_commits": len(cts),
        "commits_per_day": days, "commits_per_hour": hours,
        "stars_over_time": star_series,
        "open_issues_by_label": labels.most_common(),
    }


def merge_snapshots(data, outdir):
    """Keep a time series of forks/open issues/stars across runs."""
    snaps = []
    try:
        with open(os.path.join(outdir, "data.json"), encoding="utf-8") as f:
            snaps = json.load(f).get("snapshots", [])
    except Exception:
        pass
    snaps.append({"t": data["generated_at"], "stars": data["stars"],
                  "forks": data["forks"], "open_issues": data["open_issues"]})
    data["snapshots"] = snaps[-5000:]


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>tinglongwu stats</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#667;--card:#f6f7f9;--grid:#e3e5e8;--c1:#2563eb;--c2:#d97706;--c3:#059669;--c4:#7c3aed}
@media(prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e8e8ea;--mut:#9aa;--card:#1d2026;--grid:#2c3038;--c1:#60a5fa;--c2:#fbbf24;--c3:#34d399;--c4:#a78bfa}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif;max-width:1100px;margin:auto}
h1{font-size:20px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
.k{background:var(--card);border-radius:8px;padding:10px}.k b{display:block;font-size:26px}.k span{color:var(--mut);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px;margin-top:14px}
@media(max-width:480px){.grid{grid-template-columns:1fr}}
.c{background:var(--card);border-radius:8px;padding:10px}.c h2{font-size:14px;margin:0 0 6px}.c div{position:relative;height:240px}
small{color:var(--mut)}
</style></head><body>
<h1><a id="rn" style="color:inherit"></a> stats <small id="gen"></small></h1>
<div class="kpis" id="kpis"></div>
<div class="grid">
<div class="c"><h2>Commits per hour (last 72h, dev)</h2><div><canvas id="ch"></canvas></div></div>
<div class="c"><h2>Commits per day (dev)</h2><div><canvas id="cd"></canvas></div></div>
<div class="c"><h2>Stars over time</h2><div><canvas id="st"></canvas></div></div>
<div class="c"><h2>Forks and open issues (hourly snapshots)</h2><div><canvas id="sn"></canvas></div></div>
<div class="c"><h2>Open issues by label</h2><div><canvas id="lb"></canvas></div></div>
</div>
<script src="__CHART__"></script>
<script>
fetch('data.json').then(r=>r.json()).then(d=>{
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
Chart.defaults.color=css('--fg');Chart.defaults.borderColor=css('--grid');Chart.defaults.maintainAspectRatio=false;
document.getElementById('rn').textContent=d.repo;document.getElementById('rn').href='https://github.com/'+d.repo;
document.getElementById('gen').textContent='updated '+d.generated_at;
[['Stars',d.stars],['Forks',d.forks],['Open issues',d.open_issues],['Commits ('+d.branch+')',d.total_commits]]
.forEach(([l,v])=>{document.getElementById('kpis').insertAdjacentHTML('beforeend','<div class="k"><b>'+v+'</b><span>'+l+'</span></div>')});
const lab=a=>a.map(x=>x[0]),val=a=>a.map(x=>x[1]);
new Chart(ch,{type:'bar',data:{labels:lab(d.commits_per_hour).map(s=>s.slice(5,13).replace('T',' ')+'h'),datasets:[{data:val(d.commits_per_hour),backgroundColor:css('--c1')}]},options:{plugins:{legend:{display:false}}}});
new Chart(cd,{type:'bar',data:{labels:lab(d.commits_per_day),datasets:[{data:val(d.commits_per_day),backgroundColor:css('--c2')}]},options:{plugins:{legend:{display:false}}}});
new Chart(st,{type:'line',data:{labels:lab(d.stars_over_time).map(s=>s.slice(0,10)),datasets:[{data:val(d.stars_over_time),borderColor:css('--c3'),pointRadius:0,stepped:true,fill:false}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:8}}}}});
const sn=d.snapshots;
new Chart(document.getElementById('sn'),{type:'line',data:{labels:sn.map(s=>s.t.slice(5,16).replace('T',' ')),datasets:[{label:'Forks',data:sn.map(s=>s.forks),borderColor:css('--c4'),pointRadius:0},{label:'Open issues',data:sn.map(s=>s.open_issues),borderColor:css('--c2'),pointRadius:0}]},options:{scales:{x:{ticks:{maxTicksLimit:8}}}}});
new Chart(lb,{type:'bar',data:{labels:d.open_issues_by_label.map(x=>x[0]),datasets:[{data:d.open_issues_by_label.map(x=>x[1]),backgroundColor:css('--c4')}]},options:{indexAxis:'y',plugins:{legend:{display:false}}}});
});
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "sunososobro-hub/tinglongwu"))
    ap.add_argument("--branch", default="dev")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    data = collect(a.repo, a.branch, token())
    merge_snapshots(data, a.outdir)
    with open(os.path.join(a.outdir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(os.path.join(a.outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(HTML.replace("__CHART__", CHART_JS))
    print(f"ok: {data['stars']} stars, {data['forks']} forks, {data['open_issues']} open issues, {data['total_commits']} commits")


if __name__ == "__main__":
    sys.exit(main())
