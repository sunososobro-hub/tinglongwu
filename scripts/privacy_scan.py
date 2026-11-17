#!/usr/bin/env python3
"""Privacy gate (#1405): scan PR diffs, issues and comments against the private word list.

The list is the repo secret `TLWU_DENYLIST`, passed to this script as the environment variable
TLWU_DENYLIST_TEXT (one word or phrase per line, same format and matcher as scripts/scan_denylist.py).
Nothing here prints, logs, caches or uploads a list word: hits are reported as file:line / links, with the
match replaced by asterisks. Stdlib only; talks to the GitHub REST/GraphQL API with GITHUB_TOKEN.

  pr     --pr N [--fallback-check NAME]   added lines of PR N (API only, never checks out PR code).
                                          Exit 1 on a hit; exit 1 "denylist secret missing" when the list is
                                          missing (fail closed), unless --fallback-check is given: then the
                                          result of that check run on the PR head decides (fork PRs get no
                                          secrets under `pull_request`; `privacy-scan` runs under
                                          `pull_request_target` and has them).
  event  --name EVENT --path FILE         one issues / issue_comment / pull_request_review_comment /
                                          pull_request_target event: a hit in a comment minimizes it (OFF_TOPIC),
                                          a hit in an issue/PR title or body is replaced by *** in place; the
                                          item gets the label `privacy-hit`. No list -> does nothing (exit 0).
  sweep                                   tracked files of the checkout + every issue/PR body and comment;
                                          labels hits `privacy-hit`, prints only counts and links.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_denylist as sd  # noqa: E402

LABEL = "privacy-hit"
API = "https://api.github.com"
MISSING = "privacy-scan: denylist secret missing (TLWU_DENYLIST); failing closed"


class GitHub:
    """Minimal REST/GraphQL client. Tests replace it with a fake that has the same methods."""

    def __init__(self, repo: str, token: str | None = None):
        self.repo = repo
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")

    def _call(self, method: str, url: str, body=None, accept="application/vnd.github+json"):
        if not url.startswith("http"):
            url = API + url
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", accept)
        req.add_header("User-Agent", "tlwu-privacy-scan")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (api.github.com only)
            raw = r.read()
            return (json.loads(raw) if raw else None), r.headers.get("Link", "")

    def get(self, path: str):
        return self._call("GET", path)[0]

    def get_raw(self, path: str) -> bytes:
        url = API + path if not path.startswith("http") else path
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.raw",
                                                   "User-Agent": "tlwu-privacy-scan",
                                                   **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.read()

    def paginate(self, path: str):
        url = API + path + ("&" if "?" in path else "?") + "per_page=100"
        while url:
            data, link = self._call("GET", url)
            yield from data
            url = next((p.split(";")[0].strip(" <>") for p in link.split(",") if 'rel="next"' in p), None)

    def patch(self, path: str, body: dict):
        return self._call("PATCH", path, body)[0]

    def add_label(self, number: int):
        return self._call("POST", f"/repos/{self.repo}/issues/{number}/labels", {"labels": [LABEL]})[0]

    def minimize(self, node_id: str):
        q = ("mutation($id:ID!){minimizeComment(input:{subjectId:$id,classifier:OFF_TOPIC})"
             "{minimizedComment{isMinimized}}}")
        return self._call("POST", "/graphql", {"query": q, "variables": {"id": node_id}})[0]


def matcher() -> sd.Matcher | None:
    words = sd.env_words()
    if not words:
        return None
    m = sd.Matcher(words)
    return m if m.size else None


# ---------------------------------------------------------------- PR diff
def scan_pr_files(files: list[dict], rx: sd.Matcher, gh=None, head_sha: str = "") -> list[str]:
    """Hits in the ADDED lines of a PR's `pulls/N/files` list (removed lines never count)."""
    hits: list[str] = []
    for f in files:
        name, status = f.get("filename", "?"), f.get("status", "")
        if rx.search(name) and status != "removed":
            hits.append(f"{rx.sub(name)}:0: (file name)")
        if status == "removed":
            continue
        patch = f.get("patch")
        if patch is not None:
            lines = list(sd.added_lines(patch))
        elif gh is not None and head_sha:
            # no patch: binary, or too large for the API diff -> read the whole new file (data only, never run)
            try:
                data = gh.get_raw(f"/repos/{gh.repo}/contents/{name}?ref={head_sha}")
            except (urllib.error.URLError, OSError):
                hits.append(f"{rx.sub(name)}:0: (unreadable, no diff from the API; check it by hand)")
                continue
            if b"\0" in data[:8192]:
                continue
            lines = list(enumerate(data.decode("utf-8", "replace").splitlines(), 1))
        else:
            continue
        for n, text in lines:
            if rx.search(text):
                hits.append(f"{rx.sub(name)}:{n}: {rx.sub(text.strip())[:160]}")
    return hits


def wait_check(gh, sha: str, name: str, timeout_s: int = 900, every_s: int = 20, sleep=time.sleep) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        runs = gh.get(f"/repos/{gh.repo}/commits/{sha}/check-runs?check_name={name}&per_page=100") or {}
        runs = [r for r in runs.get("check_runs", []) if r.get("name") == name]
        done = [r for r in runs if r.get("status") == "completed"]
        if done:
            newest = max(done, key=lambda r: r.get("completed_at") or "")
            return newest.get("conclusion") == "success"
        if time.monotonic() >= deadline:
            return False
        sleep(every_s)


def cmd_pr(gh, pr: int, fallback_check: str | None = None, rx: sd.Matcher | None = None) -> int:
    rx = rx if rx is not None else matcher()
    if rx is None:
        if fallback_check:
            sha = gh.get(f"/repos/{gh.repo}/pulls/{pr}")["head"]["sha"]
            print(f"privacy-scan: no denylist here (fork PR); waiting for the `{fallback_check}` check on {sha[:8]}")
            ok = wait_check(gh, sha, fallback_check)
            print(f"privacy-scan: `{fallback_check}` {'passed' if ok else 'did not pass'}")
            return 0 if ok else 1
        print(MISSING, file=sys.stderr)
        return 1
    meta = gh.get(f"/repos/{gh.repo}/pulls/{pr}")
    files = list(gh.paginate(f"/repos/{gh.repo}/pulls/{pr}/files"))
    hits = scan_pr_files(files, rx, gh, meta["head"]["sha"])
    for h in hits:
        print(h)
    if hits:
        print(f"privacy-scan: {len(hits)} hit(s) in added lines (matched words masked)", file=sys.stderr)
        return 1
    print(f"privacy-scan: clean ({len(files)} file(s), {rx.size} list entries)")
    return 0


# ---------------------------------------------------------------- issues / comments
def handle_event(gh, name: str, ev: dict, rx: sd.Matcher | None = None) -> str:
    """Returns a short status word; never the matched text."""
    rx = rx if rx is not None else matcher()
    if rx is None:
        print("privacy-scan: no denylist configured; issues/comments are not checked")
        return "no-list"
    if name in ("issue_comment", "pull_request_review_comment"):
        c = ev.get("comment") or {}
        if not rx.search(c.get("body") or ""):
            return "clean"
        gh.minimize(c["node_id"])
        item = ev.get("issue") or ev.get("pull_request") or {}
        gh.add_label(item["number"])
        print(f"privacy-scan: hidden comment {c.get('html_url', '')}")
        return "minimized"
    if name in ("issues", "pull_request_target", "pull_request"):
        item = ev.get("issue") or ev.get("pull_request") or {}
        title, body = item.get("title") or "", item.get("body") or ""
        if not (rx.search(title) or rx.search(body)):
            return "clean"
        kind = "issues" if "issue" in ev else "pulls"
        gh.patch(f"/repos/{gh.repo}/{kind}/{item['number']}", {"title": rx.sub(title, "***"), "body": rx.sub(body, "***")})
        gh.add_label(item["number"])
        print(f"privacy-scan: masked {item.get('html_url', '')}")
        return "masked"
    return "ignored"


# ---------------------------------------------------------------- one-time sweep
def sweep(gh, rx: sd.Matcher, root: Path) -> dict:
    counts = {"files": 0, "items": 0, "comments": 0}
    file_hits: list[str] = []
    sd.scan_tracked(rx, file_hits, root)
    for h in file_hits:
        path, line = h.split(":", 2)[:2]  # path is already masked by scan_text; the text is dropped
        print(f"file: {path}:{line}")
    counts["files"] = len(file_hits)
    labelled: set[int] = set()

    def label(n: int):
        if n not in labelled:
            gh.add_label(n)
            labelled.add(n)

    for it in gh.paginate(f"/repos/{gh.repo}/issues?state=all"):
        if rx.search(it.get("title") or "") or rx.search(it.get("body") or ""):
            counts["items"] += 1
            print("item:", it.get("html_url", ""))
            label(it["number"])
    for path in (f"/repos/{gh.repo}/issues/comments", f"/repos/{gh.repo}/pulls/comments"):
        for c in gh.paginate(path):
            if rx.search(c.get("body") or ""):
                counts["comments"] += 1
                print("comment:", c.get("html_url", ""))
                url = c.get("issue_url") or c.get("pull_request_url") or ""
                if url.rstrip("/").split("/")[-1].isdigit():
                    label(int(url.rstrip("/").split("/")[-1]))
    print("privacy-scan sweep: " + " ".join(f"{k}={v}" for k, v in counts.items()) + f" labelled={len(labelled)}")
    return counts


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pr")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--fallback-check")
    e = sub.add_parser("event")
    e.add_argument("--name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    e.add_argument("--path", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    sub.add_parser("sweep")
    a = ap.parse_args(argv)
    gh = GitHub(a.repo)
    if a.cmd == "pr":
        return cmd_pr(gh, a.pr, a.fallback_check)
    if a.cmd == "event":
        handle_event(gh, a.name, json.loads(Path(a.path).read_text("utf-8")))
        return 0
    rx = matcher()
    if rx is None:
        print(MISSING, file=sys.stderr)
        return 1
    sweep(gh, rx, sd.ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
