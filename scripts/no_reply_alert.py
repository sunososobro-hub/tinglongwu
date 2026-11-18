#!/usr/bin/env python3
"""Alert the owner when an outside issue/PR got no human reply (issue #1503).

Selection rules are pure functions (unit-tested). Dry run by default; --post comments once on the
pinned `[health] pipeline check log` issue (#61) listing items not alerted before.

  python scripts/no_reply_alert.py [--repo OWNER/NAME] [--post]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime

REPO = "sunososobro-hub/tinglongwu"
OWNER = "sunososobro-hub"
LOG_ISSUE = 61
BOT_WORDS = ("[bot]", "coderabbit", "github-actions", "dependabot", "claude")
MARK = "no-reply-alert:"


def is_bot(user: dict | None) -> bool:
    if not user:
        return True
    login = (user.get("login") or "").lower()
    return user.get("type") == "Bot" or any(w in login for w in BOT_WORDS)


def hours_since(iso: str, now: datetime) -> float:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (now - t).total_seconds() / 3600


def has_human_reply(author: str, replies: list[dict]) -> bool:
    """replies: comments / reviews / review comments, each with a `user` dict."""
    return any((r.get("user") or {}).get("login") != author and not is_bot(r.get("user")) for r in replies)


def threshold_hours(is_pr: bool, author_has_merged_pr: bool) -> int:
    return 24 if is_pr and not author_has_merged_pr else 48


def select_items(items: list[dict], now: datetime, replies_of, merged_authors: set[str]) -> list[dict]:
    """items: issue dicts from the issues API (PRs carry `pull_request`). replies_of(item) -> list."""
    out = []
    for it in items:
        user = it.get("user")
        author = (user or {}).get("login", "")
        if author == OWNER or is_bot(user):
            continue
        is_pr = "pull_request" in it
        hrs = hours_since(it["created_at"], now)
        if hrs < threshold_hours(is_pr, author in merged_authors):
            continue
        if has_human_reply(author, replies_of(it)):
            continue
        out.append({"number": it["number"], "title": it["title"], "author": author,
                    "hours": int(hrs), "url": it["html_url"]})
    return out


def alerted_numbers(comment_bodies: list[str]) -> set[int]:
    seen: set[int] = set()
    for body in comment_bodies:
        if MARK not in body:
            continue
        seen.update(int(n) for n in re.findall(r"^#(\d+) ", body, flags=re.M))
    return seen


def new_items(selected: list[dict], already: set[int]) -> list[dict]:
    seen = set(already)
    out = []
    for s in selected:
        if s["number"] not in seen:
            seen.add(s["number"])
            out.append(s)
    return out


def plain_title(title: str) -> str:
    """Render an outside title as a literal code span: no mentions, links, autolinks, entities or HTML."""
    return "`" + " ".join(title.replace("`", "'").split()) + "`"


def format_comment(items: list[dict]) -> str:
    lines = [f"{MARK} @{OWNER} outside items with no human reply:", ""]
    lines += [f"#{i['number']} {plain_title(i['title'])} | {i['author']} | {i['hours']} h | {i['url']}" for i in items]
    return "\n".join(lines)


def _gh(path: str):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(p.stdout)


def _pages(path: str) -> list:
    sep = "&" if "?" in path else "?"
    out, n = [], 1
    while True:
        page = _gh(f"{path}{sep}per_page=100&page={n}")
        out += page
        if len(page) < 100:
            return out
        n += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--post", action="store_true")
    a = ap.parse_args()
    now = datetime.now(UTC)
    items = _pages(f"repos/{a.repo}/issues?state=open")
    merged = {p["user"]["login"] for p in _pages(f"repos/{a.repo}/pulls?state=closed") if p.get("merged_at") and p.get("user")}

    def replies_of(it):
        n = it["number"]
        r = _pages(f"repos/{a.repo}/issues/{n}/comments")
        if "pull_request" in it:
            r += _pages(f"repos/{a.repo}/pulls/{n}/reviews") + _pages(f"repos/{a.repo}/pulls/{n}/comments")
        return r

    sel = select_items(items, now, replies_of, merged)
    bodies = [c["body"] for c in _pages(f"repos/{a.repo}/issues/{LOG_ISSUE}/comments")
              if (c.get("user") or {}).get("login") in (OWNER, "github-actions[bot]")]
    fresh = new_items(sel, alerted_numbers(bodies))
    if not fresh:
        print("no new items")
        return 0
    text = format_comment(fresh)
    if not a.post:
        print(text)
        return 0
    print(f"posting {len(fresh)} item(s)")  # titles stay out of runner command output
    p = subprocess.run(["gh", "api", f"repos/{a.repo}/issues/{LOG_ISSUE}/comments", "--input", "-"],
                       input=json.dumps({"body": text}), capture_output=True, text=True, check=False)
    if p.returncode:
        print(f"gh post failed: {p.stderr.strip()[:300]}")
        return p.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
