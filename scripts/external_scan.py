#!/usr/bin/env python3
"""Read-only scan of outside-contributor activity (issue #360).

Prints 4 sections: open non-owner issues, open non-owner PRs, non-owner comments
with no later owner comment in the same thread, and forks with commits not in upstream.
Only GET requests via `gh api` are used.
"""
import json
import subprocess
import sys

REPO = "sunososobro-hub/tinglongwu"
OWNER = "sunososobro-hub"


def gh_fetch(path):
    """GET a REST path through `gh api` (paginated), return the parsed JSON list/dict."""
    out = subprocess.run(["gh", "api", "--paginate", path] if "compare/" not in path else ["gh", "api", path], capture_output=True, text=True, timeout=50, check=True).stdout
    try:
        return json.loads(out)
    except ValueError:  # --paginate may emit concatenated arrays
        dec, i, items = json.JSONDecoder(), 0, []
        out = out.strip()
        while i < len(out):
            obj, i = dec.raw_decode(out, i)
            items.extend(obj if isinstance(obj, list) else [obj])
            while i < len(out) and out[i].isspace():
                i += 1
        return items


def scan(fetch, repo=REPO, owner=OWNER):
    issues = fetch(f"repos/{repo}/issues?state=open&per_page=100")
    ext_issues = [i for i in issues if "pull_request" not in i and i["user"]["login"] != owner]
    pulls = fetch(f"repos/{repo}/pulls?state=open&per_page=100")
    ext_prs = [p for p in pulls if p["user"]["login"] != owner]

    unanswered = []
    for i in issues:
        comments = fetch(f"repos/{repo}/issues/{i['number']}/comments?per_page=100")
        last_owner = max((n for n, c in enumerate(comments) if c["user"]["login"] == owner), default=-1)
        for n, c in enumerate(comments):
            if c["user"]["login"] != owner and n > last_owner:
                unanswered.append((i["number"], c["user"]["login"]))
                break

    forks = []
    default = fetch(f"repos/{repo}")
    base = default.get("default_branch", "dev") if isinstance(default, dict) else "dev"
    for f in fetch(f"repos/{repo}/forks?per_page=100"):
        fo, fb = f["owner"]["login"], f.get("default_branch", base)
        try:
            cmp_ = fetch(f"repos/{repo}/compare/{base}...{fo}:{fb}?per_page=1")
            ahead = cmp_.get("ahead_by", 0) if isinstance(cmp_, dict) else 0
        except Exception:  # unreachable/diverged fork: treat as not ahead
            ahead = 0
        forks.append((f["full_name"], ahead))
    return {"issues": ext_issues, "prs": ext_prs, "comments": unanswered, "forks": forks}


def render(r):
    lines = ["== Open non-owner issues =="]
    lines += [f"#{i['number']} {i['title']} (@{i['user']['login']})" for i in r["issues"]] or ["(none)"]
    lines += ["== Open non-owner PRs =="]
    lines += [f"#{p['number']} {p['title']} (@{p['user']['login']})" for p in r["prs"]] or ["(none)"]
    lines += ["== Non-owner comments without a later owner reply =="]
    lines += [f"#{n} by @{u}" for n, u in r["comments"]] or ["(none)"]
    lines += ["== Forks with commits not in upstream =="]
    ahead = [(n, a) for n, a in r["forks"] if a > 0]
    lines += [f"{n} ahead by {a}" for n, a in ahead] or ["(none)"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(scan(gh_fetch)))
    sys.exit(0)
