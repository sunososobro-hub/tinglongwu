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


def week(t):
    d = t.date() - timedelta(days=t.weekday())
    return d.isoformat()


def weeks_range(start, now):
    out, d = [], start.date() - timedelta(days=start.weekday())
    while d <= now.date():
        out.append(d.isoformat())
        d += timedelta(days=7)
    return out


def median(v):
    v = sorted(v)
    if not v:
        return None
    m = len(v) // 2
    return round(v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2, 2)


def community(repo, branch, tok, commits, now):
    """Weekly CHAOSS-style series. Each series is [[week, value|None], ...]."""
    allissues = pages(f"/repos/{repo}/issues?state=all", tok)
    issues = [i for i in allissues if "pull_request" not in i]
    prs = pages(f"/repos/{repo}/pulls?state=all", tok)
    comments = pages(f"/repos/{repo}/issues/comments?sort=created&direction=asc", tok)
    # first response = first comment by someone other than the issue author
    first = {}
    by_num = {i["number"]: i for i in issues}
    for c in comments:
        n = int(c["issue_url"].rsplit("/", 1)[1])
        i = by_num.get(n)
        if i and n not in first and c["user"]["login"] != i["user"]["login"]:
            first[n] = parse(c["created_at"])
    # contributors: first activity per login (commits, PRs, issues)
    firsts = {}
    def seen(login, t):
        if login and (login not in firsts or t < firsts[login]):
            firsts[login] = t
    for c in commits:
        seen((c.get("author") or {}).get("login"), parse(c["commit"]["committer"]["date"]))
    for p in prs:
        seen(p["user"]["login"], parse(p["created_at"]))
    for i in issues:
        seen(i["user"]["login"], parse(i["created_at"]))
    # releases / tags
    rel, relnames = [], set()
    for r in pages(f"/repos/{repo}/releases", tok):
        relnames.add(r["tag_name"])
        if r.get("published_at"):
            rel.append(parse(r["published_at"]))
    for t in pages(f"/repos/{repo}/tags", tok)[:100]:
        if t["name"] in relnames:
            continue
        try:
            cm = get(t["commit"]["url"], tok)
            rel.append(parse(cm["commit"]["committer"]["date"]))
        except Exception:
            pass
    stamps = [parse(i["created_at"]) for i in issues] + [parse(p["created_at"]) for p in prs] + list(firsts.values()) + rel
    stamps += [parse(c["commit"]["committer"]["date"]) for c in commits]
    ws = weeks_range(min(stamps), now) if stamps else []
    def cnt(times):
        c = Counter(week(t) for t in times)
        return [[w, c.get(w, 0)] for w in ws]
    def med(pairs):
        b = {}
        for t, v in pairs:
            b.setdefault(week(t), []).append(v)
        return [[w, median(b[w]) if w in b else None] for w in ws]
    resp = [(parse(i["created_at"]), (first[i["number"]] - parse(i["created_at"])).total_seconds() / 3600)
            for i in issues if i["number"] in first]
    # PR time-to-merge bucketed by merge week
    merge = [(parse(p["merged_at"]), (parse(p["merged_at"]) - parse(p["created_at"])).total_seconds() / 3600)
             for p in prs if p.get("merged_at")]
    closed = [parse(i["closed_at"]) for i in issues if i.get("closed_at")]
    return {
        "weeks": ws,
        "first_response_h": med(resp),
        "pr_merge_h": med(merge),
        "new_contributors": cnt(firsts.values()),
        "issues_opened": cnt([parse(i["created_at"]) for i in issues]),
        "issues_closed": cnt(closed),
        "releases": cnt(rel),
        "total_contributors": len(firsts),
        "total_releases": len(rel),
        "prs_merged": len(merge),
        "median_first_response_h": median([v for _, v in resp]),
        "median_pr_merge_h": median([v for _, v in merge]),
    }


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
    com = community(repo, branch, tok, commits, now)
    return {
        "community": com,
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
.c.w{grid-column:1/-1}.c{background:var(--card);border-radius:8px;padding:10px}.c h2{font-size:14px;margin:0 0 6px}.c div{position:relative;height:240px}
small{color:var(--mut)}
</style></head><body>
<h1><a id="rn" style="color:inherit"></a> stats <small id="gen"></small></h1>
<div class="kpis" id="kpis"></div>
<div class="grid">
<div class="c"><h2>Commits per hour (last 72h, dev)</h2><div><canvas id="ch"></canvas></div></div>
<div class="c"><h2>Commits per day (dev)</h2><div><canvas id="cd"></canvas></div></div>
<div class="c"><h2>Stars over time</h2><div><canvas id="st"></canvas></div></div>
<div class="c"><h2>Forks and open issues (hourly snapshots)</h2><div><canvas id="sn"></canvas></div></div>
<div class="c"><h2>Median time to first response on new issues (hours, weekly)</h2><div><canvas id="fr"></canvas></div></div>
<div class="c"><h2>Median PR time-to-merge (hours, weekly by merge date)</h2><div><canvas id="pm"></canvas></div></div>
<div class="c"><h2>New contributors per week</h2><div><canvas id="nc"></canvas></div></div>
<div class="c"><h2>Issues opened vs closed per week</h2><div><canvas id="oc"></canvas></div></div>
<div class="c"><h2>Releases / tags per week</h2><div><canvas id="rl"></canvas></div></div>
<div class="c"><h2>Open issues by label</h2><div><canvas id="lb"></canvas></div></div>
</div>
<script src="__CHART__"></script>
<script>
fetch('data.json').then(r=>r.json()).then(d=>{
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
Chart.defaults.color=css('--fg');Chart.defaults.borderColor=css('--grid');Chart.defaults.maintainAspectRatio=false;
document.getElementById('rn').textContent=d.repo;document.getElementById('rn').href='https://github.com/'+d.repo;
document.getElementById('gen').textContent='updated '+d.generated_at;
const cm=d.community,hr=v=>v==null?'n/a':v+'h';
[['Stars',d.stars],['Open issues',d.open_issues],['Median 1st response',hr(cm.median_first_response_h)],['Median PR merge',hr(cm.median_pr_merge_h)],['Contributors',cm.total_contributors]]
.forEach(([l,v])=>{document.getElementById('kpis').insertAdjacentHTML('beforeend','<div class="k"><b>'+v+'</b><span>'+l+'</span></div>')});
const lab=a=>a.map(x=>x[0]),val=a=>a.map(x=>x[1]);
new Chart(ch,{type:'bar',data:{labels:lab(d.commits_per_hour).map(s=>s.slice(5,13).replace('T',' ')+'h'),datasets:[{data:val(d.commits_per_hour),backgroundColor:css('--c1')}]},options:{plugins:{legend:{display:false}}}});
new Chart(cd,{type:'bar',data:{labels:lab(d.commits_per_day),datasets:[{data:val(d.commits_per_day),backgroundColor:css('--c2')}]},options:{plugins:{legend:{display:false}}}});
new Chart(st,{type:'line',data:{labels:lab(d.stars_over_time).map(s=>s.slice(0,10)),datasets:[{data:val(d.stars_over_time),borderColor:css('--c3'),pointRadius:0,stepped:true,fill:false}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:8}}}}});
const W=cm.weeks,wl=W.map(w=>w.slice(5)),opt={plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:10}}}};
new Chart(fr,{type:'line',data:{labels:wl,datasets:[{data:val(cm.first_response_h),borderColor:css('--c1'),spanGaps:true,pointRadius:3}]},options:opt});
new Chart(pm,{type:'line',data:{labels:wl,datasets:[{data:val(cm.pr_merge_h),borderColor:css('--c3'),spanGaps:true,pointRadius:3}]},options:opt});
new Chart(nc,{type:'bar',data:{labels:wl,datasets:[{data:val(cm.new_contributors),backgroundColor:css('--c4')}]},options:opt});
new Chart(oc,{type:'line',data:{labels:wl,datasets:[{label:'Opened',data:val(cm.issues_opened),borderColor:css('--c2'),pointRadius:2},{label:'Closed',data:val(cm.issues_closed),borderColor:css('--c3'),pointRadius:2}]},options:{scales:{x:{ticks:{maxTicksLimit:10}}}}});
new Chart(rl,{type:'bar',data:{labels:wl,datasets:[{data:val(cm.releases),backgroundColor:css('--c1')}]},options:opt});
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
