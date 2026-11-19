#!/usr/bin/env python3
"""Privacy gate (#1405): scan PR diffs, issues and comments for personal data and secrets.

Two kinds of checks, both needing no secret to run:
  * built-in patterns (always on): email addresses (bot / noreply / example.* addresses allowed), phone numbers
    (TW 09xx-xxx-xxx, +886..., any +E.164), TW national IDs (letter, 1 or 2, eight digits; checksum-valid), Luhn-valid 13-19
    digit card numbers with a card-network prefix, and API keys / tokens (ghp_ / github_pat_ / sk- / AKIA /
    xox* / private-key blocks);
  * optional extra words: the repo secret `TLWU_DENYLIST`, passed as the environment variable TLWU_DENYLIST_TEXT
    (one word or phrase per line, same format and matcher as scripts/scan_denylist.py). Missing -> built-in
    patterns only; a clean result passes.
Nothing here prints, logs, caches or uploads a matched value: hits are reported as file:line / links, with the
match replaced by asterisks. Stdlib only; talks to the GitHub REST/GraphQL API with GITHUB_TOKEN.

  pr     --pr N [--fallback-check NAME]   added lines of PR N (API only, never checks out PR code). Exit 1 on a
                                          hit. With --fallback-check and no list (fork PRs get no secrets under
                                          `pull_request`), the built-in patterns run here and, when clean, the
                                          result of that check run on the PR head decides (`privacy-scan` runs
                                          under `pull_request_target` and has the list when it is set).
  event  --name EVENT --path FILE         one issues / issue_comment / pull_request_review_comment /
                                          pull_request_target event: a hit in a comment minimizes it (OFF_TOPIC),
                                          a hit in an issue/PR title or body is replaced by *** in place; the
                                          item gets the label `privacy-hit`.
  sweep                                   tracked files of the checkout + every issue/PR body and comment;
                                          labels hits `privacy-hit`, prints only counts and links.
"""
from __future__ import annotations

import argparse
import datetime
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_denylist as sd  # noqa: E402

LABEL = "privacy-hit"
API = "https://api.github.com"


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

    def post_status(self, sha: str, state: str, context: str, description: str):
        body = {"state": state, "context": context, "description": description[:140]}
        return self._call("POST", f"/repos/{self.repo}/statuses/{sha}", body)[0]

    def add_label(self, number: int):
        return self._call("POST", f"/repos/{self.repo}/issues/{number}/labels", {"labels": [LABEL]})[0]

    def minimize(self, node_id: str):
        q = ("mutation($id:ID!){minimizeComment(input:{subjectId:$id,classifier:OFF_TOPIC})"
             "{minimizedComment{isMinimized}}}")
        r = self._call("POST", "/graphql", {"query": q, "variables": {"id": node_id}})[0] or {}
        mc = ((r.get("data") or {}).get("minimizeComment") or {}).get("minimizedComment") or {}
        if r.get("errors") or not mc.get("isMinimized"):  # GraphQL errors come back with HTTP 200
            raise RuntimeError("privacy-scan: minimizeComment failed; the comment is still visible")
        return r


# ---------------------------------------------------------------- built-in patterns (no secret needed)
ALLOWED_EMAIL_DOMAINS = ("users.noreply.github.com", "example.com", "example.org", "example.net")
ALLOWED_EMAILS = {"noreply@anthropic.com", "noreply@github.com", "git@github.com"}
CARD_PREFIX = re.compile(r"4|5[1-5]|2[2-7]|3[0-9]|6")  # Visa, Mastercard, Amex 34/37, Diners 30/36/38, JCB 35, Discover/UnionPay


def _email_ok(m: re.Match) -> bool:
    addr = m.group(0).lower()
    dom = addr.rsplit("@", 1)[1]
    return not (addr in ALLOWED_EMAILS or any(dom == d or dom.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS))


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _card_ok(m: re.Match) -> bool:
    digits = re.sub(r"\D", "", m.group(0))
    return 13 <= len(digits) <= 19 and bool(CARD_PREFIX.match(digits)) and _luhn(digits)


_TWID_LETTER = {c: v for c, v in zip("ABCDEFGHJKLMNPQRSTUVXYWZIO", range(10, 36))}


def _twid_ok(m: re.Match) -> bool:
    s = m.group(0)
    n = _TWID_LETTER[s[0]]
    digits = [n // 10, n % 10] + [int(c) for c in s[1:]]
    weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1]
    return sum(d * w for d, w in zip(digits, weights)) % 10 == 0


# (kind, regex, extra validator or None). Every match is masked; the kind is printed, never the value.
PATTERNS: list[tuple[str, re.Pattern, object]] = [
    # the whole PEM block through its END line; an unterminated block is masked to the end of the text
    # (the END label must equal the BEGIN label: a stray END of another type does not stop the mask)
    ("private key", re.compile(r"-----BEGIN ((?:[A-Z0-9]+ )*)PRIVATE KEY-----"
                               r"(?:[\s\S]*?-----END \1PRIVATE KEY-----|[\s\S]*\Z)"), None),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"), None),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), None),
    ("AWS key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), None),
    ("email", re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), _email_ok),
    ("phone", re.compile(r"(?<![\w+])09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)"), None),
    ("phone", re.compile(r"(?<![\w+])\+[1-9]\d{0,2}[- ]?(?:\(?\d{1,4}\)?[- ]?)?\d{3,4}[- ]?\d{3,4}(?!\d)"),
     lambda m: 8 <= len(re.sub(r"\D", "", m.group(0))) <= 15),
    ("national ID", re.compile(r"\b[A-Z][12]\d{8}\b"), _twid_ok),
    ("card number", re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?![\w-])"), _card_ok),
]


class Builtin:
    """The built-in pattern checks, with the same spans/search/sub interface as scan_denylist.Matcher."""

    size = len(PATTERNS)

    def hits(self, text: str) -> list[tuple[int, int, str]]:
        out = []
        for kind, rx, ok in PATTERNS:
            for m in rx.finditer(text or ""):
                if ok is None or ok(m):
                    out.append((m.start(), m.end(), kind))
        return out

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [(a, b) for a, b, _ in self.hits(text)]


class Combined:
    """Built-in patterns plus the optional private word list; overlapping spans are merged before masking."""

    def __init__(self, words: sd.Matcher | None = None):
        self.builtin = Builtin()
        self.words = words if words is not None and words.size else None
        self.size = self.builtin.size + (self.words.size if self.words else 0)

    def describe(self) -> str:
        n = self.words.size if self.words else 0
        return f"{self.builtin.size} built-in patterns, {n} list entries" + ("" if n else " (TLWU_DENYLIST not set)")

    def spans(self, text: str) -> list[tuple[int, int]]:
        raw = sorted(self.builtin.spans(text) + (self.words.spans(text) if self.words else []))
        merged: list[tuple[int, int]] = []
        for a, b in raw:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        return merged

    def search(self, text: str) -> bool:
        return bool(self.spans(text))

    def sub(self, text: str, repl=None) -> str:
        out, last = [], 0
        for a, b in self.spans(text):
            out.append(text[last:a])
            out.append("*" * (b - a) if repl is None else repl)
            last = b
        out.append(text[last:])
        return "".join(out)


def matcher() -> Combined:
    """Built-in patterns always; the TLWU_DENYLIST words on top when the secret is set."""
    words = sd.env_words()
    return Combined(sd.Matcher(words) if words else None)


# ---------------------------------------------------------------- PR diff
def _pure_rename(f: dict, gh=None, base_sha: str = "") -> bool:
    """#1653: a rename whose content is byte-identical (git similarity 100%) adds no line, so there is nothing
    new to scan. Strict on purpose: the API must say `renamed`, name the old path, and report exactly 0
    additions / deletions / changes as ints. A missing count, a CRLF or any other edit (> 0 changes) and a
    `copied` / `added` file still go through the full scan. A mode-only change keeps the content identical.
    The counts alone are not proof: GitHub reports 0 / 0 / 0 and no patch for a file it treats as binary (e.g. a
    `.gitattributes` `binary` / `-diff` added in the PR). So the head blob sha (`sha`) must also equal the blob
    sha of `previous_filename` on the base commit. No base sha, a mismatch or any API error -> scan (fail closed)."""
    if f.get("status") != "renamed" or not f.get("previous_filename"):
        return False
    counts = [f.get(k) for k in ("additions", "deletions", "changes")]
    if not all(type(c) is int and c == 0 for c in counts):
        return False
    head_blob = f.get("sha")
    if not (isinstance(head_blob, str) and head_blob and gh is not None and base_sha):
        return False
    try:
        old = gh.get(f"/repos/{gh.repo}/contents/{urllib.parse.quote(f['previous_filename'], safe='/')}"
                     f"?ref={urllib.parse.quote(base_sha, safe='')}")
    except Exception:  # noqa: BLE001 - any failure means "not proven identical": scan it
        return False
    return isinstance(old, dict) and old.get("type", "file") == "file" and old.get("sha") == head_blob


def scan_pr_files(files: list[dict], rx, gh=None, head_sha: str = "", base_sha: str = "") -> list[str]:
    """Hits in the ADDED lines of a PR's `pulls/N/files` list (removed lines never count)."""
    hits: list[str] = []
    for f in files:
        name, status = f.get("filename", "?"), f.get("status", "")
        if rx.search(name) and status != "removed":
            hits.append(f"{rx.sub(name)}:0: (file name)")
        if status == "removed" or _pure_rename(f, gh, base_sha):
            continue  # the file name itself was checked above
        patch = f.get("patch")
        if patch is not None:
            lines = list(sd.added_lines(patch))
        elif gh is not None and head_sha:
            # no patch: binary, or too large for the API diff -> read the whole new file (data only, never run)
            try:
                data = gh.get_raw(f"/repos/{gh.repo}/contents/{urllib.parse.quote(name, safe='/')}?ref={head_sha}")
            except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
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
    """Result of `name` on commit `sha`: a commit status with that context (what the pull_request_target job
    posts on the PR head, since its own check run lands on the base commit), or a check run with that name."""
    deadline = time.monotonic() + timeout_s
    while True:
        st = gh.get(f"/repos/{gh.repo}/commits/{sha}/status") or {}
        mine = [x for x in st.get("statuses", []) if x.get("context") == name]  # newest first
        if mine and mine[0].get("state") in ("success", "failure", "error"):
            return mine[0]["state"] == "success"
        runs = gh.get(f"/repos/{gh.repo}/commits/{sha}/check-runs?check_name={name}&per_page=100") or {}
        runs = [r for r in runs.get("check_runs", []) if r.get("name") == name]
        done = [r for r in runs if r.get("status") == "completed"]
        if done:
            newest = max(done, key=lambda r: r.get("completed_at") or "")
            return newest.get("conclusion") == "success"
        if time.monotonic() >= deadline:
            return False
        sleep(every_s)


def cmd_pr(gh, pr: int, fallback_check: str | None = None, rx=None, report_status: str | None = None) -> int:
    rc, sha = _cmd_pr(gh, pr, fallback_check, rx)
    if report_status and sha:  # pull_request_target: publish on the commit that was SCANNED, never a later head
        gh.post_status(sha, "success" if rc == 0 else "failure", report_status,
                       "privacy scan clean" if rc == 0 else "privacy scan found hits (see the privacy-scan job)")
    return rc


def _cmd_pr(gh, pr: int, fallback_check: str | None = None, rx=None) -> tuple[int, str]:
    """(exit code, scanned head sha). The sha is empty when the head moved during the scan (nothing to publish)."""
    rx = rx if rx is not None else matcher()
    info = gh.get(f"/repos/{gh.repo}/pulls/{pr}")
    sha, base_sha = info["head"]["sha"], (info.get("base") or {}).get("sha", "")
    files = list(gh.paginate(f"/repos/{gh.repo}/pulls/{pr}/files"))
    if gh.get(f"/repos/{gh.repo}/pulls/{pr}")["head"]["sha"] != sha:
        print("privacy-scan: the PR head moved during the scan; the run for the new head decides", file=sys.stderr)
        return 1, ""
    hits = scan_pr_files(files, rx, gh, sha, base_sha)
    for h in hits:
        print(h)
    if hits:
        print(f"privacy-scan: {len(hits)} hit(s) in added lines (matched values masked)", file=sys.stderr)
        return 1, sha
    desc = rx.describe() if hasattr(rx, "describe") else f"{rx.size} entries"
    print(f"privacy-scan: clean ({len(files)} file(s), {desc})")
    if fallback_check and getattr(rx, "words", True) is None:
        # fork PR: no secret here, so the private words are checked by the `privacy-scan` run that has them
        print(f"privacy-scan: no word list here (fork PR); waiting for the `{fallback_check}` check on {sha[:8]}")
        ok = wait_check(gh, sha, fallback_check)
        print(f"privacy-scan: `{fallback_check}` {'passed' if ok else 'did not pass'}")
        return (0 if ok else 1), sha
    return 0, sha


# ---------------------------------------------------------------- issues / comments
def handle_event(gh, name: str, ev: dict, rx=None) -> str:
    """Returns a short status word; never the matched text."""
    rx = rx if rx is not None else matcher()
    if name in ("issue_comment", "pull_request_review_comment"):
        c = ev.get("comment") or {}
        if not rx.search(c.get("body") or ""):
            return "clean"
        item = ev.get("issue") or ev.get("pull_request") or {}
        label_error = None
        try:
            gh.add_label(item["number"])  # first, so the item is flagged even if hiding fails
        except Exception as e:  # noqa: BLE001 - still hide the comment, then fail the job
            label_error = e
        gh.minimize(c["node_id"])  # raises (job red) unless GitHub confirms the comment is hidden
        if label_error is not None:
            raise RuntimeError("privacy-scan: comment hidden but labelling failed") from label_error
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
def sweep(gh, rx, root: Path) -> dict:
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


# ---------------------------------------------------------------- scheduled scan of recent comments (#1498)
def scan_recent(gh, rx, since: str) -> dict:
    """Hide hits among comments created/edited since [since] (ISO 8601). Owner / agent comments start no
    per-event runner job (#1498); this scheduled run covers them. Prints links only, never matched text.
    One failing comment never stops the scan; the job fails at the end if any hit stayed visible or unlabelled."""
    counts = {"comments": 0, "hidden": 0}
    failures: list[str] = []
    for path in (f"/repos/{gh.repo}/issues/comments", f"/repos/{gh.repo}/pulls/comments"):
        try:
            for c in gh.paginate(f"{path}?since={since}"):
                counts["comments"] += 1
                if not rx.search(c.get("body") or ""):
                    continue
                url = c.get("issue_url") or c.get("pull_request_url") or ""
                number = url.rstrip("/").split("/")[-1]
                if number.isdigit():
                    try:
                        gh.add_label(int(number))  # first, so the item is flagged even if hiding fails
                    except Exception:  # noqa: BLE001 - still hide the comment, fail the job at the end
                        failures.append(f"label {c.get('html_url', '')}")
                try:
                    gh.minimize(c["node_id"])
                except Exception:  # noqa: BLE001 - keep scanning; reported below
                    failures.append(f"hide {c.get('html_url', '')}")
                    continue
                counts["hidden"] += 1
                print("privacy-scan: hidden comment", c.get("html_url", ""))
        except Exception:  # noqa: BLE001 - a failed page must not skip the other feed
            failures.append(f"feed {path.rsplit('/', 2)[-2]}/{path.rsplit('/', 1)[-1]}")
    print("privacy-scan recent: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    if failures:
        raise RuntimeError("privacy-scan recent: failed: " + "; ".join(failures))
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
    p.add_argument("--report-status", help="commit status context to set on the PR head with the result")
    e = sub.add_parser("event")
    e.add_argument("--name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    e.add_argument("--path", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    sub.add_parser("sweep")
    r = sub.add_parser("recent")
    r.add_argument("--minutes", type=int, default=90, help="look back this far (schedule is every 30 min; 90 survives one dropped run)")
    a = ap.parse_args(argv)
    gh = GitHub(a.repo)
    if a.cmd == "pr":
        return cmd_pr(gh, a.pr, a.fallback_check, report_status=a.report_status)
    if a.cmd == "event":
        handle_event(gh, a.name, json.loads(Path(a.path).read_text("utf-8")))
        return 0
    if a.cmd == "recent":
        since = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=a.minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        scan_recent(gh, matcher(), since)
        return 0
    sweep(gh, matcher(), sd.ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
