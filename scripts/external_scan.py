#!/usr/bin/env python3
"""List every piece of outside-contributor activity on the repo (read-only, issue #360).

Prints 4 sections:
  1. open issues opened by someone other than the owner
  2. open PRs opened by someone other than the owner
  3. threads (issue or PR, open or closed) where a non-owner commented after the owner's last comment
  4. forks with commits not in upstream (fork, branch, ahead count); a fork that cannot be checked is
     listed as "check failed: <reason>", never silently dropped

Bots (user.type == "Bot", e.g. coderabbitai[bot]) are not outside contributors and are ignored.

Reads only: `gh api` GET requests. A fork's branch heads come from `gh api repos/<fork>/branches`; if
that is refused (e.g. a token scoped to this repo only), they come from the fork's public git ref list
(`https://github.com/<fork>.git/info/refs`, read with urllib). Each head is compared inside this repo
(`repos/<repo>/compare/<base>...<sha>`), which works for any commit in the fork network and avoids
the cross-fork `owner:branch` selector. If upstream history was rewritten after the fork was made
(GitHub answers "No common ancestor"), the fork's commits are matched to upstream ones by author
time + subject, and the commits above the newest shared one are counted.

  python scripts/external_scan.py [--repo OWNER/NAME] [--owner LOGIN]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

REPO = "sunososobro-hub/tinglongwu"
OWNER = "sunososobro-hub"


class FetchError(Exception):
    pass


def _gh_get(path: str):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=50, check=False)
    if p.returncode != 0:
        msg = (p.stderr.strip() or p.stdout.strip()).splitlines()
        raise FetchError(msg[-1] if msg else f"gh api exit {p.returncode}")
    return json.loads(p.stdout)


def gh_fetch(path: str, paginate: bool = False, max_pages: int = 200):
    """GET a REST path with `gh api`. paginate=True returns a list of pages.

    Pages are walked with &page=N (not `--paginate`): GitHub's Link header points at numeric
    repositories/{id} URLs, which some proxies refuse."""
    if not paginate:
        return _gh_get(path)
    per = int(re.search(r"per_page=(\d+)", path).group(1)) if "per_page=" in path else 30
    pages = []
    for n in range(1, max_pages + 1):
        page = _gh_get(f"{path}{'&' if '?' in path else '?'}page={n}")
        pages.append(page)
        if len(page) < per:
            return pages
    raise FetchError(f"{path}: more than {max_pages} pages")


def git_refs(full_name: str) -> dict[str, str]:
    """Branch heads from the public git smart-HTTP ref advertisement (read-only, no token)."""
    import urllib.request  # only used when gh cannot read the fork

    url = f"https://github.com/{full_name}.git/info/refs?service=git-upload-pack"
    req = urllib.request.Request(url, headers={"User-Agent": "git/2.43 external_scan"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_refs(r.read().decode("utf-8", "replace"))


def parse_refs(text: str) -> dict[str, str]:
    heads = {}
    for m in re.finditer(r"([0-9a-f]{40}) refs/heads/([^\s\x00]+)", text):
        heads[m.group(2)] = m.group(1)
    return heads


def _items(fetch, path):
    """All items of a paginated list endpoint."""
    return [x for page in fetch(path, paginate=True) for x in page]


def _human(u: dict | None) -> bool:
    return bool(u) and u.get("type") != "Bot" and not u.get("login", "").endswith("[bot]")


def _msg(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


def fork_heads(fetch, full_name: str, refs=git_refs) -> dict[str, str]:
    try:
        return {b["name"]: b["commit"]["sha"] for b in _items(fetch, f"repos/{full_name}/branches?per_page=100")}
    except Exception as e1:  # noqa: BLE001 - fall back, then report both reasons
        try:
            return refs(full_name)
        except Exception as e2:
            raise FetchError(f"branches: {_msg(e1)}; git refs: {_msg(e2)}") from e2


def commit_key(c: dict) -> tuple[str, str]:
    """Identity of a commit that survives a history rewrite: author instant + subject.

    Not the author name: this repo's rewrite also changed author names (Alonso -> sunososobro-hub)."""
    a = c["commit"]["author"]
    when = datetime.fromisoformat(a["date"]).astimezone(UTC).isoformat()
    return when, (c["commit"]["message"].splitlines() or [""])[0]


def upstream_keys(fetch, repo: str, bases: list[str]) -> set:
    return {commit_key(c) for b in bases for c in _items(fetch, f"repos/{repo}/commits?sha={b}&per_page=100")}


def count_unshared(fetch, repo: str, sha: str, keys: set, max_pages: int = 20) -> tuple[int, bool, str]:
    """Walk the fork branch's history (first-parent order as the API lists it) until a commit whose
    key is upstream. Returns (commits before it, found a shared commit, head subject)."""
    n, head = 0, ""
    for page in range(1, max_pages + 1):
        cs = fetch(f"repos/{repo}/commits?sha={sha}&per_page=100&page={page}")
        for c in cs:
            head = head or commit_key(c)[1]
            if commit_key(c) in keys:
                return n, True, head
            n += 1
        if len(cs) < 100:
            break
    return n, False, head


def check_branch(fetch, repo: str, bases: list[str], sha: str, keys=None) -> tuple[str, int | str]:
    """("ahead", n) | ("even", 0) | ("unrelated", text) | ("error", msg).

    ahead = commits reachable from sha and from none of the upstream bases (min over bases).
    When upstream history was rewritten (GitHub: "No common ancestor"), commits are matched by
    commit_key instead of SHA; `keys` is a zero-arg callable returning the upstream key set."""
    aheads, errors, unrelated = [], [], False
    for base in bases:
        try:
            c = fetch(f"repos/{repo}/compare/{base}...{sha}?per_page=1")
            aheads.append(int(c["ahead_by"]))
        except Exception as e:  # noqa: BLE001 - recorded, shown in the report
            if "no common ancestor" in str(e).lower():
                unrelated = True
            else:
                errors.append(f"{base}: {_msg(e)}")
    if aheads:
        n = min(aheads)
        return ("ahead", n) if n else ("even", 0)
    if unrelated and keys is not None:
        try:
            n, shared, head = count_unshared(fetch, repo, sha, keys())
        except Exception as e:  # noqa: BLE001
            return ("error", f"rewritten upstream history, walk failed: {_msg(e)}")
        if shared:
            return ("ahead", n) if n else ("even", 0)
        return ("unrelated", f"{n}+ commits, no commit shared with upstream, head {sha[:7]} {head}")
    if unrelated:
        return ("unrelated", f"no common ancestor with upstream, head {sha[:7]}")
    return ("error", "; ".join(errors) or "no upstream base to compare with")


def scan_forks(fetch, repo: str, bases: list[str], refs=git_refs, workers: int = 8) -> list[tuple]:
    """[(fork full_name, branch or None, status, value)] for every fork; errors included."""
    forks = _items(fetch, f"repos/{repo}/forks?per_page=100")
    lock, cache = threading.Lock(), {}

    def keys():  # fetched once, only if some fork needs it
        with lock:
            if "k" not in cache:
                cache["k"] = upstream_keys(fetch, repo, bases)
            return cache["k"]

    def one(f):
        name = f["full_name"]
        try:
            heads = fork_heads(fetch, name, refs)
        except Exception as e:  # noqa: BLE001
            return [(name, None, "error", _msg(e))]
        if not heads:
            return [(name, None, "error", "no branches found")]
        return [(name, b, *check_branch(fetch, repo, bases, sha, keys)) for b, sha in sorted(heads.items())]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [row for rows in ex.map(one, forks) for row in rows]


def _thread_no(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[1])


def unanswered(fetch, repo: str, owner: str) -> list[tuple[int, list[str], str]]:
    """[(thread number, [non-owner logins], last comment time)] where a human non-owner spoke last.

    Covers issue/PR conversation comments and PR review (code) comments, open and closed threads."""
    threads: dict[int, list[tuple[str, str]]] = {}
    for c in _items(fetch, f"repos/{repo}/issues/comments?per_page=100"):
        threads.setdefault(_thread_no(c["issue_url"]), []).append((c["created_at"], c["user"]))
    for c in _items(fetch, f"repos/{repo}/pulls/comments?per_page=100"):
        threads.setdefault(_thread_no(c["pull_request_url"]), []).append((c["created_at"], c["user"]))
    out = []
    for n, cs in threads.items():
        cs.sort(key=lambda t: t[0])
        last_owner = max((t for t, u in cs if u and u.get("login") == owner), default="")
        late = [(t, u["login"]) for t, u in cs if _human(u) and u["login"] != owner and t > last_owner]
        if late:
            out.append((n, sorted({lg for _, lg in late}), late[-1][0]))
    return sorted(out)


def scan(fetch, repo: str = REPO, owner: str = OWNER, refs=git_refs) -> dict:
    issues = _items(fetch, f"repos/{repo}/issues?state=open&per_page=100")
    ext_issues = [i for i in issues if "pull_request" not in i and _human(i["user"]) and i["user"]["login"] != owner]
    pulls = _items(fetch, f"repos/{repo}/pulls?state=open&per_page=100")
    ext_prs = [p for p in pulls if _human(p["user"]) and p["user"]["login"] != owner]
    info = fetch(f"repos/{repo}")
    bases = list(dict.fromkeys([info.get("default_branch") or "main", "dev"]))
    return {"issues": ext_issues, "prs": ext_prs, "comments": unanswered(fetch, repo, owner),
            "forks": scan_forks(fetch, repo, bases, refs), "bases": bases}


def render(r: dict) -> str:
    L = ["== Open non-owner issues =="]
    L += [f"#{i['number']} {i['title']} (@{i['user']['login']})" for i in r["issues"]] or ["(none)"]
    L += ["", "== Open non-owner PRs =="]
    L += [f"#{p['number']} {p['title']} (@{p['user']['login']})" for p in r["prs"]] or ["(none)"]
    L += ["", "== Non-owner comments without a later owner reply =="]
    L += [f"#{n} by {', '.join('@' + u for u in us)} (last {t})" for n, us, t in r["comments"]] or ["(none)"]
    L += ["", f"== Forks with commits not in upstream ({' or '.join(r['bases'])}) =="]
    rows, even = [], set()
    for name, branch, status, val in r["forks"]:
        where = f"{name}:{branch}" if branch else name
        if status == "ahead":
            rows.append(f"{where} ahead by {val}")
        elif status == "unrelated":
            rows.append(f"{where} unrelated history: {val}")
        elif status == "error":
            rows.append(f"{where} check failed: {val}")
        else:
            even.add(name)
    flagged = {name for name, _, s, _ in r["forks"] if s != "even"}
    L += rows or ["(none)"]
    ok = sorted(even - flagged)
    L += [f"({len({f[0] for f in r['forks']})} forks checked; even with upstream: {', '.join(ok) or 'none'})"]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--owner", default=OWNER)
    a = ap.parse_args(argv)
    print(render(scan(gh_fetch, a.repo, a.owner)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
