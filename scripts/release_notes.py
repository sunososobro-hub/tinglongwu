#!/usr/bin/env python3
"""Release automation helpers (#414): next version, release plan, release notes. Used by the workflows
release-pr.yml (dev -> main release PR) and release.yml (build + publish after that PR is merged).

    python scripts/release_notes.py next-version                 latest v* tag -> the next one
    python scripts/release_notes.py version --title "release: vX" the title's version, else the next one
    python scripts/release_notes.py plan                         release.yml: publish? which tag? (GITHUB_OUTPUT lines)
    python scripts/release_notes.py notes --version vX [--since vY] [--verification report.md] [--out notes.md]

Versioning: vX.Y.Z-beta.N -> vX.Y.Z-beta.(N+1), vX.Y.Z -> vX.Y.(Z+1). The release PR title `release: vA.B.C` sets
the tag instead (the owner may edit it before merging). Thanks: every author of a PR (any state: some contributions
were applied by hand, e.g. #108) or issue who is not the repo owner and not a bot.
Network access only through the `gh` CLI (GH_TOKEN); the pure functions below are unit-tested.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
TITLE_RE = re.compile(r"^release:\s*(v\S+)\s*$", re.I)
TITLE_PREFIX = "release:"
GPU_LABEL = "gpu-validated-locally"
MAX_ASSET = 2 * 1024**3  # GitHub Release per-asset limit (2 GiB)


# ---------------------------------------------------------------- versions

def parse_tag(tag: str):
    m = TAG_RE.match(tag)
    if not m:
        return None
    pre = m.group(4)
    # sort key: a release sorts after its pre-releases; pre-release parts compare numerically where possible
    pre_key = (1,) if pre is None else (0, *[(0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")])
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre_key)


def latest_tag(tags: list[str]) -> str | None:
    good = [t for t in tags if parse_tag(t)]
    return max(good, key=parse_tag) if good else None


def next_version(tags: list[str]) -> str:
    last = latest_tag(tags)
    if last is None:
        return "v0.1.0"
    m = TAG_RE.match(last)
    major, minor, patch, pre = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    if pre:
        n = re.match(r"^(.*?)(\d+)$", pre)
        if n:
            return f"v{major}.{minor}.{patch}-{n.group(1)}{int(n.group(2)) + 1}"
        return f"v{major}.{minor}.{patch}"
    return f"v{major}.{minor}.{patch + 1}"


def title_version(title: str) -> str | None:
    m = TITLE_RE.match(title.strip())
    return m.group(1) if m and parse_tag(m.group(1)) else None


def release_version(title: str, tags: list[str]) -> str:
    """Tag for a merged release PR: the title's version if valid, else the next one. Never an existing tag."""
    v = title_version(title) or next_version(tags)
    if v in tags:
        raise SystemExit(f"tag {v} already exists; edit the release PR title to a new version")
    last = latest_tag(tags)
    if last and parse_tag(v) <= parse_tag(last):
        raise SystemExit(f"{v} is not newer than the latest tag {last}")
    return v


# ---------------------------------------------------------------- plan (release.yml)

def release_pr_of(pulls: list[dict]) -> dict | None:
    """The merged dev -> main release PR among the PRs GitHub associates with a commit, if any."""
    for p in pulls:
        if (p.get("merged_at") and p.get("base", {}).get("ref") == "main" and p.get("head", {}).get("ref") == "dev"
                and p.get("title", "").lower().startswith(TITLE_PREFIX)):
            return p
    return None


def plan(event: str, ref_type: str, ref_name: str, tags: list[str], pulls: list[dict]) -> dict:
    """publish = create the Release; tag = its tag; pr = the release PR number; gpu_local = GPU label present."""
    out = {"publish": "false", "tag": "", "pr": "", "gpu_local": "false", "reason": ""}
    if event == "workflow_dispatch":
        out.update(tag=next_version(tags) + "-dryrun", reason="workflow_dispatch: dry run, nothing is published")
    elif event == "push" and ref_type == "tag":
        if not parse_tag(ref_name):
            raise SystemExit(f"bad tag: {ref_name}")
        out.update(publish="true", tag=ref_name, reason=f"tag {ref_name} pushed by hand")
    elif event == "push" and ref_type == "branch" and ref_name == "main":
        pr = release_pr_of(pulls)
        if pr is None:
            out["reason"] = "push to main that is not a merged dev -> main 'release:' PR: nothing to release"
        else:
            labels = {lab.get("name") for lab in pr.get("labels", [])}
            out.update(publish="true", tag=release_version(pr["title"], tags), pr=str(pr["number"]),
                       gpu_local=str(GPU_LABEL in labels).lower(), reason=f"release PR #{pr['number']} merged")
    else:
        out["reason"] = f"{event} {ref_type} {ref_name}: nothing to release"
    return out


# ---------------------------------------------------------------- notes

def _bot(author: dict | None) -> bool:
    login = (author or {}).get("login", "")
    return bool((author or {}).get("is_bot")) or login.endswith("[bot]") or login.startswith("app/") or not login


def contributors(prs: list[dict], issues: list[dict], owner: str) -> dict[str, list[str]]:
    """login -> ["#108 (PR)", "#34 (issue)", ...] for every non-owner, non-bot author, ordered by first item."""
    items = [(p["number"], "PR", p.get("author")) for p in prs] + [(i["number"], "issue", i.get("author")) for i in issues]
    out: dict[str, list[str]] = {}
    for num, kind, author in sorted(items, key=lambda x: x[0]):
        if _bot(author) or author["login"].lower() == owner.lower():
            continue
        out.setdefault(author["login"], []).append(f"#{num} ({kind})")
    return out


def gpu_line(gpu_local: bool, pr: str = "") -> str:
    if gpu_local:
        where = f" (label `{GPU_LABEL}` on #{pr})" if pr else ""
        return ("**GPU: validated locally** by the maintainer on this commit before release" + where +
                ". The CI runner has no GPU, so the published zip itself was only tested on the CPU path.")
    return ("**GPU: not validated.** The CI runner has no GPU. The CUDA path was checked statically only "
            "(every CUDA DLL dependency resolves); the published zip was smoke- and self-tested on the CPU path.")


def render(version: str, since: str | None, changes: list[dict], thanks: dict[str, list[str]],
           verification: str = "", gpu: str = "") -> str:
    lines = [f"## tinglongWu {version}", ""]
    lines += [f"### Changes since {since}" if since else "### Changes", ""]
    if changes:
        for c in changes[:150]:
            lines.append(f"- {c['title']} (#{c['number']})")
        if len(changes) > 150:
            lines.append(f"- ... and {len(changes) - 150} more")
    else:
        lines.append("- (no merged PRs found)")
    lines += ["", "### Windows package", ""]
    lines.append(verification.strip() if verification.strip() else
                 "`tinglongwu-win64.zip` is built by the `release` workflow on GitHub Actions (windows-latest) after "
                 "this PR is merged; size, SHA256 and the test results are added to the Release notes then.")
    if gpu:
        lines += ["", gpu]
    lines += ["", "### Thanks", "",
              "Thank you to everyone outside the core team who reported problems, tested and sent fixes:", ""]
    for login, its in thanks.items():
        lines.append(f"- @{login}: {', '.join(its)}")
    if not thanks:
        lines.append("- (none yet)")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- gh / git I/O

def gh_json(*args: str):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout or "null")


def git_tags() -> list[str]:
    r = subprocess.run(["git", "tag", "--list", "v*"], capture_output=True, text=True, check=True)
    return r.stdout.split()


def tag_date(tag: str) -> str | None:
    r = subprocess.run(["git", "log", "-1", "--format=%cI", tag], capture_output=True, text=True)
    return r.stdout.strip() or None


def repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


def cmd_notes(a) -> str:
    rep = repo()
    owner = a.owner or rep.split("/")[0]
    since = a.since or latest_tag([t for t in git_tags() if t != a.version])
    date = tag_date(since) if since else None
    search = ["--search", f"merged:>{date}"] if date else []
    changes = gh_json("pr", "list", "-R", rep, "--state", "merged", "--base", "dev", "-L", "500", *search,
                      "--json", "number,title")
    changes = sorted(changes, key=lambda c: c["number"])
    prs = gh_json("pr", "list", "-R", rep, "--state", "all", "-L", "2000", "--json", "number,author")
    issues = gh_json("issue", "list", "-R", rep, "--state", "all", "-L", "2000", "--json", "number,author")
    ver = Path(a.verification).read_text("utf-8") if a.verification else ""
    gpu = gpu_line(a.gpu_local, a.pr) if a.verification else ""
    return render(a.version, since, changes, contributors(prs, issues, owner), ver, gpu)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("next-version")
    v = sub.add_parser("version", help="the version in a 'release: vX' title, else the next one")
    v.add_argument("--title", default="")
    sub.add_parser("plan")
    n = sub.add_parser("notes")
    n.add_argument("--version", required=True)
    n.add_argument("--since")
    n.add_argument("--owner")
    n.add_argument("--verification", help="markdown from packaging/release_check.py")
    n.add_argument("--gpu-local", action="store_true")
    n.add_argument("--pr", default="")
    n.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "next-version":
        print(next_version(git_tags()))
    elif a.cmd == "version":
        print(title_version(a.title) or next_version(git_tags()))
    elif a.cmd == "plan":
        event, ref_type, ref_name = (os.environ.get(k, "") for k in ("GITHUB_EVENT_NAME", "GITHUB_REF_TYPE",
                                                                      "GITHUB_REF_NAME"))
        pulls = []
        if event == "push" and ref_type == "branch":
            pulls = gh_json("api", f"repos/{repo()}/commits/{os.environ['GITHUB_SHA']}/pulls")
        for k, v in plan(event, ref_type, ref_name, git_tags(), pulls).items():
            print(f"{k}={v}")
    else:
        text = cmd_notes(a)
        if a.out:
            Path(a.out).write_text(text, "utf-8")
        else:
            sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
