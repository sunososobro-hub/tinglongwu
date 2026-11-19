"""#1472: open the patch-bump PR from a scheduled Action (rules section 14 "Version bumps" cadence).

The cadence is pure conditions, so no AI run is needed. `decide` checks them; `--apply` then opens the issue
`[release] bump __version__ to X.Y.Z`, the branch `fix/<issue>` and the bump PR (the `__version__` line plus one
generated `## vX.Y.Z` section in docs/whats_new.md) that passes `scripts/release_notes.py bump-check`.

Usage: python scripts/bump_action.py            dry run: prints the decision and the PR body
       python scripts/bump_action.py --apply    opens the issue, branch and PR (needs `gh` + GH_TOKEN, git push)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_notes import WHATS_NEW_FILE, app_version, throttle_level  # noqa: E402

REPO = "sunososobro-hub/tinglongwu"
TAIPEI = timezone(timedelta(hours=8))
SLOT_HOURS = (11, 17)  # first wake after 11:00 / after 17:00 Asia/Taipei (two releases a day, #1274)
MAX_BULLETS = 3
FALLBACK_BULLET = "- Fixes and improvements; the full list of changes is in the release notes below."
_SKIP_TITLE = re.compile(r"^(?:release|ci|chore|docs|test|rules|infra|maint)\b|\[(?:ci|infra|release|deps)\]", re.I)


def next_patch(version: str) -> str:
    x, y, z = (int(p) for p in version.split("."))
    return f"{x}.{y}.{z + 1}"


def slot_start(now: datetime) -> datetime:
    """Most recent 11:00 / 17:00 Asia/Taipei at or before `now`."""
    local = now.astimezone(TAIPEI)
    starts = [local.replace(hour=h, minute=0, second=0, microsecond=0) for h in SLOT_HOURS]
    starts.append(starts[-1] - timedelta(days=1))  # yesterday's 17:00 covers the night before 11:00
    return max(s for s in starts if s <= local)


def decide(*, now: datetime, version: str, tags: set[str], ahead: int, test_green: bool, open_bump_pr: bool,
           hotfix_active: bool, hotfix_backmerge_open: bool, last_bump_at: datetime | None,
           p01_fix_since_release: bool, throttle: int | None) -> tuple[bool, str]:
    """Rules section 14 cadence: (open a bump?, reason). Every condition must hold; the last is the schedule slot."""
    if throttle is not None and throttle < 1:
        return False, "throttle level 0 (off)"
    if f"v{version}" not in tags:
        return False, f"tag v{version} does not exist (the last bump is not released yet)"
    if ahead < 1:
        return False, "dev is not ahead of main by a merged PR"
    if not test_green:
        return False, "the `test` check on the dev tip is not green"
    if open_bump_pr:
        return False, "a bump PR is already open"
    if hotfix_active or hotfix_backmerge_open:
        return False, "a hotfix run or its back-merge PR is still open"
    if p01_fix_since_release:
        return True, "a P0/P1 bug fix merged since the last release"
    start = slot_start(now)
    if last_bump_at is not None and last_bump_at >= start:
        return False, f"already bumped since {start.isoformat()}"
    return True, f"first wake after {start.astimezone(TAIPEI):%H:%M} Asia/Taipei with no bump since"


def bullets(titles: list[str]) -> list[str]:
    """Up to 3 plain-language bullets from merged PR titles: no PR/issue numbers, no TODO, no infra work."""
    out: list[str] = []
    for t in titles:
        t = re.sub(r"\s*\(#\d+\)\s*$", "", t.strip())
        t = re.sub(r"^(?:fix|feat)\s*#\d+\s*:\s*", "", t, flags=re.I)
        t = re.sub(r"\s*#\d+\b", "", t).strip(" -:")
        # Markdown / list markers would make bump_exempt reject the bullet; a leading digit that is title text stays
        t = re.sub(r"^(?:(?:[#>*+=<\-]|[0-9]+[.)](?=\s|$))\s*)*", "", t)
        if not t or _SKIP_TITLE.search(t) or "TODO" in t:
            continue
        line = "- " + t[0].upper() + t[1:].rstrip(".") + "."
        if line not in out:
            out.append(line)
        if len(out) == MAX_BULLETS:
            break
    return out or [FALLBACK_BULLET]


def bump_files(init_text: str, whats_new_text: str, new_version: str, bullet_lines: list[str]) -> tuple[str, str]:
    """New text of src/core/tinglongwu/__init__.py (only the version line changes) and docs/whats_new.md (one section added
    above the newest one, below the intro and the example comment)."""
    new_init, n = re.subn(r'^(__version__\s*=\s*")[^"]+(")', rf"\g<1>{new_version}\2", init_text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("no __version__ line to bump")
    section = f"## v{new_version}\n" + "\n".join(bullet_lines) + "\n\n"
    # first version heading outside HTML comments (the file's example heading sits inside one)
    blank = lambda c: re.sub(r"[^\n]", " ", c.group(0))  # keep offsets and line starts
    visible = re.sub(r"<!--.*?-->", blank, whats_new_text, flags=re.S)
    visible = re.sub(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$", blank, visible, flags=re.S | re.M)
    m = re.search(r"^## v\d", visible, re.M)
    at = m.start() if m else len(whats_new_text)
    prefix = whats_new_text[:at] if whats_new_text[:at].endswith("\n\n") else whats_new_text[:at].rstrip("\n") + "\n\n"
    return new_init, prefix + section + whats_new_text[at:]


def pr_body(issue: int, version: str, reason: str, bullet_lines: list[str]) -> str:
    return (f"Owner comments: none\n\nCadence check passed: {reason}. Diff = `__version__` patch bump + one generated "
            f"`## v{version}` section in `{WHATS_NEW_FILE}` (bump-check exempt).\n\n" + "\n".join(bullet_lines) +
            f"\n\nRefs #{issue}\n\ntlwu-usage: model=none tool_calls=0 minutes=0")


# ---- gh / git glue (not unit tested; the decision and file functions above are) ----

def _gh(*args: str) -> object:
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=ROOT).stdout.strip()


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def gather(now: datetime) -> dict:
    tags = set(_git("tag").split())
    tip = _git("rev-parse", "origin/dev")  # one dev commit for version, checks, history and the bump base
    version = app_version(_git("show", f"{tip}:src/core/tinglongwu/__init__.py"))
    cmp_ = _gh("api", f"repos/{REPO}/compare/main...{tip}")
    checks = _gh("api", f"repos/{REPO}/commits/{tip}/check-runs?per_page=100")["check_runs"]
    test = [c for c in checks if c["name"] == "test"]
    pulls = _gh("api", f"repos/{REPO}/pulls?state=open&base=dev&per_page=100", "--paginate", "--slurp")
    pulls = [x for page in pulls for x in page]
    bump_open = any(p["title"].startswith("release: bump __version__") for p in pulls)
    backmerge = any(p["head"]["ref"].startswith("hotfix/") for p in pulls)
    hot = sum(_gh("api", f"repos/{REPO}/actions/workflows/hotfix.yml/runs?status={st}&per_page=1")["total_count"]
              for st in ("queued", "waiting", "in_progress"))
    last = _gh("api", "-X", "GET", f"repos/{REPO}/pulls", "-f", "state=closed", "-f", "per_page=100")
    bumps = [_ts(p["merged_at"]) for p in last if p.get("merged_at") and p["title"].startswith("release: bump")]
    body = _gh("api", f"repos/{REPO}/issues/574")["body"]
    return dict(now=now, version=version, tags=tags, ahead=cmp_["ahead_by"],
                test_green=bool(test) and test[0]["conclusion"] == "success", open_bump_pr=bump_open,
                hotfix_active=hot > 0, hotfix_backmerge_open=backmerge,
                last_bump_at=max(bumps, default=None), p01_fix_since_release=f"v{version}" in tags and _p01_since(version),
                throttle=throttle_level(body))


def _p01_since(version: str) -> bool:
    """A merged PR since the last release tag whose linked issue is a P0/P1 bug."""
    nums = set(re.findall(r"#(\d+)", _git("log", f"v{version}..origin/dev", "--format=%s")))
    for n in sorted(nums, key=int)[:50]:
        try:
            it = _gh("api", f"repos/{REPO}/issues/{n}")
        except subprocess.CalledProcessError:
            continue
        names = {lb["name"] for lb in it["labels"]}
        if "bug" in names and names & {"P0", "P1"}:
            return True
    return False


def apply(version: str, reason: str) -> None:
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("worktree has tracked changes; refusing to open a bump")
    new = next_patch(version)
    titles = _git("log", f"v{version}..origin/dev", "--format=%s").splitlines()
    lines = bullets(titles)
    issue = _gh("api", f"repos/{REPO}/issues", "-X", "POST", "-f", f"title=[release] bump __version__ to {new}",
                "-f", "body=Why: release cadence (rules section 14).\nGoal: patch bump PR for the next release.\n"
                "Acceptance: `scripts/release_notes.py bump-check` exits 0 on the PR.\nVerify: "
                "python scripts/release_notes.py bump-check --pr <N>", "-f", "labels[]=maint", "-f", "labels[]=P2",
                "-f", "labels[]=area:ci", "-f", "labels[]=mode:shared", "-f", "labels[]=status:review",
                "-f", "labels[]=model:sonnet", "-f", "labels[]=ok-to-work")["number"]
    branch = f"fix/{issue}"
    _git("checkout", "-B", branch, "origin/dev")
    init_p, wn_p = ROOT / "src" / "core" / "tinglongwu" / "__init__.py", ROOT / WHATS_NEW_FILE
    init, wn = bump_files(init_p.read_text("utf-8"), wn_p.read_text("utf-8"), new, lines)
    init_p.write_text(init, "utf-8")
    wn_p.write_text(wn, "utf-8")
    _git("config", "user.name", "sunososobro-hub")
    _git("config", "user.email", "243713070+sunososobro-hub@users.noreply.github.com")
    _git("add", "src/core/tinglongwu/__init__.py", WHATS_NEW_FILE)
    _git("commit", "-m", f"release: bump __version__ to {new}")
    _git("push", "-u", "origin", branch)
    _gh("api", f"repos/{REPO}/pulls", "-X", "POST", "-f", f"title=release: bump __version__ to {new}",
        "-f", f"head={branch}", "-f", "base=dev", "-f", f"body={pr_body(issue, new, reason, lines)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    now = datetime.now(UTC)
    facts = gather(now)
    ok, reason = decide(**facts)
    print(f"bump: {'yes' if ok else 'no'} ({reason})")
    if ok:
        new = next_patch(facts["version"])
        lines = bullets(_git("log", f"v{facts['version']}..origin/dev", "--format=%s").splitlines())
        print(pr_body(0, new, reason, lines).replace("#0", "#<issue>"))
        if args.apply:
            apply(facts["version"], reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
