#!/usr/bin/env python3
"""Release automation helpers (#414) for release-pr.yml (the dev -> main release PR) and release.yml (build and
publish after the owner merges that PR).

    python scripts/release_notes.py pr-body [--existing FILE] --out F   release PR title (stdout) and body (F)
    python scripts/release_notes.py plan                               release.yml: publish? tag? (GITHUB_OUTPUT lines)
    python scripts/release_notes.py notes --version vX --base F --verification R [--pr N] [--hw-run URL] --out F

Version: tinglongwu/__init__.py `__version__` is the single source (#462; the setup wizard is named after it), so
the release PR title is `release: v<__version__>` and a merged release PR whose title names another version (or
none) publishes nothing. If that tag already exists, the PR body says to bump `__version__` on dev first.
Publish gates (owner, #414): only a merged dev -> main `release:` PR; ci, e2e, windows-build and hw-tests green on
the released dev commit (hw-tests runs on the owner's GPU runner, so it also stands for the GPU check); a tag that
exists already must point at the same commit. A pushed pre-release tag vX.Y.Z-suffix on main or dev (#112) also
publishes (as a pre-release, never `latest`) when it equals v<__version__> and the same four checks are green on it.
Thanks: every non-owner, non-bot author of a PR (any state) or issue.
Network only through the `gh` CLI (GH_TOKEN); the pure functions are unit-tested.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
TITLE_RE = re.compile(r"^release:\s*(v\S+)\s*$", re.I)
REQUIRED_CHECKS = ("ci", "e2e", "windows-build", "hw-tests")
WHATS_NEW = "## What's new"


def app_version(init_text: str) -> str:
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.M)
    if not m:
        raise SystemExit("no __version__ in tinglongwu/__init__.py")
    return m.group(1)


def parse_tag(tag: str):
    m = TAG_RE.match(tag)
    if not m:
        return None
    pre = m.group(4)  # a release sorts after its pre-releases; numeric parts compare as numbers
    pre_key = (1,) if pre is None else (0, *[(0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")])
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), pre_key


def latest_tag(tags: list[str]) -> str | None:
    good = [t for t in tags if parse_tag(t)]
    return max(good, key=parse_tag) if good else None


def title_version(title: str) -> str | None:
    m = TITLE_RE.match(title.strip())
    return m.group(1) if m and parse_tag(m.group(1)) else None


def release_tag(title: str, version: str, tags: dict[str, str], sha: str) -> str:
    """Tag for a merged release PR; tags = {tag: commit sha}. SystemExit when it must not be published."""
    v = title_version(title)
    if v is None:
        raise SystemExit(f"release PR title {title!r} has no valid version (release: vX.Y.Z)")
    if v != f"v{version}":
        raise SystemExit(f"title says {v} but tinglongwu.__version__ is {version}; bump __version__ on dev, "
                         "let release-pr.yml update the title, then merge again")
    if v in tags:
        if tags[v] != sha:
            raise SystemExit(f"tag {v} already exists at {tags[v][:12]}, not at the released commit {sha[:12]}")
        return v  # re-run of the same release
    last = latest_tag(list(tags))
    if last and parse_tag(v) <= parse_tag(last):
        raise SystemExit(f"{v} is not newer than the latest tag {last}")
    return v


def release_pr_of(pulls: list[dict], repo: str) -> dict | None:
    """The merged dev -> main release PR (from this repo, not a fork) among the PRs of a commit."""
    for p in pulls:
        head, base = p.get("head") or {}, p.get("base") or {}
        if (p.get("merged_at") and base.get("ref") == "main" and head.get("ref") == "dev"
                and (head.get("repo") or {}).get("full_name") == repo
                and p.get("title", "").strip().lower().startswith("release:")):
            return p
    return None


def check_problems(runs: list[dict], required=REQUIRED_CHECKS) -> list[str]:
    """Owner gate: the newest run of each required workflow on the commit must have succeeded."""
    out = []
    for name in required:
        mine = [r for r in runs if r.get("name") == name]
        if not mine:
            out.append(f"{name}: no run on this commit")
            continue
        r = max(mine, key=lambda r: r.get("id", 0))
        if r.get("status") != "completed" or r.get("conclusion") != "success":
            out.append(f"{name}: {r.get('status')}/{r.get('conclusion')} {r.get('html_url', '')}".rstrip())
    return out


def prerelease_tag(tag: str, version: str, tags: dict[str, str], sha: str) -> str:
    """#112: a pushed pre-release tag (vX.Y.Z-suffix, on main or dev; release.yml checks the branch). SystemExit
    when it must not be published. A plain vX.Y.Z only comes from the release PR."""
    m = TAG_RE.match(tag)
    if not m or m.group(4) is None:
        raise SystemExit(f"tag {tag}: only a pre-release tag (vX.Y.Z-suffix) is released on a tag push; "
                         "a plain vX.Y.Z comes from merging the release PR")
    if tag != f"v{version}":
        raise SystemExit(f"tag {tag} but tinglongwu.__version__ is {version} at that commit; the setup wizard is "
                         "named after __version__, so set it to the tag's version first")
    if tags.get(tag, sha) != sha:
        raise SystemExit(f"tag {tag} points at {tags[tag][:12]}, not at {sha[:12]}")
    return tag


def plan(event: str, ref_name: str, sha: str, version: str, tags: dict[str, str], pulls: list[dict], repo: str,
         runs_of=lambda sha: [], published=lambda tag: False, ref_type: str = "branch") -> dict:
    """publish = tag + Release; build = build and check the Windows zip; head = released dev commit."""
    out = {"publish": "false", "build": "false", "tag": "", "pr": "", "head": "", "hw_run": "", "reason": ""}
    if event == "workflow_dispatch":
        out.update(build="true", tag=f"v{version}", reason=f"workflow_dispatch on {ref_name}: dry run, publishes nothing")
        return out
    if event == "push" and ref_type == "tag":
        tag = prerelease_tag(ref_name, version, tags, sha)
        if published(tag):
            out.update(tag=tag, reason=f"release {tag} is already published")
            return out
        runs = runs_of(sha)
        bad = check_problems(runs)
        if bad:
            raise SystemExit(f"pre-release tag {tag}: required checks not green on {sha[:12]}: " + "; ".join(bad))
        hw = max((r for r in runs if r.get("name") == "hw-tests"), key=lambda r: r.get("id", 0))
        out.update(publish="true", build="true", tag=tag, head=sha, hw_run=hw.get("html_url", ""),
                   reason=f"pre-release tag {tag} pushed (#112)")
        return out
    if not (event == "push" and ref_name == "main"):
        out["reason"] = f"{event} {ref_name}: nothing to release (only a merged release PR publishes)"
        return out
    pr = release_pr_of(pulls, repo)
    if pr is None:
        out["reason"] = "push to main that is not a merged dev -> main 'release:' PR: nothing to release"
        return out
    tag = release_tag(pr["title"], version, tags, sha)
    if published(tag):  # re-run after success: nothing to do (release.yml never rewrites a published release)
        out.update(tag=tag, reason=f"release {tag} is already published")
        return out
    head = pr["head"]["sha"]
    runs = runs_of(head)
    bad = check_problems(runs)
    if bad:
        raise SystemExit(f"release PR #{pr['number']}: required checks not green on {head[:12]}: " + "; ".join(bad))
    hw = max((r for r in runs if r.get("name") == "hw-tests"), key=lambda r: r.get("id", 0))
    out.update(publish="true", build="true", tag=tag, pr=str(pr["number"]), head=head, hw_run=hw.get("html_url", ""),
               reason=f"release PR #{pr['number']} merged")
    return out


def _bot(author: dict | None) -> bool:
    login = (author or {}).get("login", "")
    return bool((author or {}).get("is_bot")) or login.endswith("[bot]") or login.startswith("app/") or not login


def contributors(prs: list[dict], issues: list[dict], owner: str) -> dict[str, list[tuple[int, str]]]:
    """login -> [(number, url), ...] for every non-owner, non-bot author of a PR or issue."""
    items = [(i["number"], i.get("url", ""), i.get("author")) for i in prs + issues]
    out: dict[str, list[tuple[int, str]]] = {}
    for num, url, author in sorted(items, key=lambda x: x[0]):
        if _bot(author) or author["login"].lower() == owner.lower():
            continue
        out.setdefault(author["login"], []).append((num, url))
    return out


def thanks_md(thanks: dict[str, list[tuple[int, str]]]) -> str:
    # links without '@': the body is rewritten on every dev push and must not notify people each time
    lines = ["### Thanks", "", "Thank you to everyone outside the core team who reported problems, tested and sent fixes:",
             ""]
    for login, items in thanks.items():
        refs = ", ".join(f"[#{n}]({u})" if u else f"#{n}" for n, u in items)
        lines.append(f"- [{login}](https://github.com/{login}): {refs}")
    return "\n".join(lines + (["- (none yet)"] if not thanks else [])) + "\n"


def changes_md(since: str | None, changes: list[dict]) -> str:
    lines = [f"### Changes since {since}" if since else "### Changes", ""]
    lines += [f"- {c['title']} (#{c['number']})" for c in changes[:150]] or ["- (no merged PRs found)"]
    if len(changes) > 150:
        lines.append(f"- ... and {len(changes) - 150} more")
    return "\n".join(lines) + "\n"


def whats_new(body: str) -> str | None:
    """The `## What's new` section of a release PR body (the owner may edit it), None if absent or still TODO.
    It ends at the next generated section, so the owner's own ### subsections are kept."""
    m = re.search(r"^## What's new[ \t]*\n(.*?)(?=^#{1,2} |^### (?:Changes|Thanks)\b|^---|\Z)", body or "", re.M | re.S)
    text = m.group(1).strip() if m else ""
    return text if text and "TODO" not in text else None


def default_whats_new(since: str | None) -> str:
    return f"- Fixes and improvements{f' since {since}' if since else ''}; the full list is below."


def pr_body(version: str, tags: list[str], since: str | None, changes: list[dict], thanks: dict,
            existing: str = "", dev_sha: str = "") -> str:
    tag = f"v{version}"
    lines = []
    if tag in tags:
        lines += [f"> **Not releasable yet:** tag `{tag}` already exists. Bump `__version__` in "
                  "`tinglongwu/__init__.py` on dev; this PR then updates itself.", ""]
    lines += [WHATS_NEW, whats_new(existing) or default_whats_new(since), "", changes_md(since, changes),
              thanks_md(thanks), "---",
              f"Merging this PR releases `{tag}` (= `tinglongwu.__version__`). The `release` workflow then checks that "
              "ci, e2e, windows-build and hw-tests are green on this PR's head commit, builds the Windows zip on "
              "windows-latest, runs check_dist_dlls, the llama CPU-instruction check, `--smoke` and `--selftest`, "
              "tags `main` and publishes the GitHub Release with the setup wizard. Edit **What's new** above before "
              "merging; it becomes the release's What's new.", "",
              f"Generated by release-pr.yml (#414) from dev {dev_sha[:7]}."]
    return "\n".join(lines) + "\n"


def gpu_line(hw_run: str, head: str) -> str:
    if hw_run:
        return (f"**GPU:** validated by [hw-tests]({hw_run}) on the maintainer's self-hosted NVIDIA runner for commit "
                f"`{head[:12]}`. The zip below was built on a GPU-less CI runner and tested there on the CPU path only.")
    return ("**GPU: not validated.** The CI runner has no GPU; the CUDA path was checked statically (every CUDA DLL "
            "dependency resolves) and the zip was tested on the CPU path only.")


def release_notes(base: str, pr_text: str, since: str | None, verification: str, hw_run: str, head: str,
                  changes: list[dict], thanks: dict) -> str:
    """render_support.py --notes output with What's new filled in, plus the Windows checks, changes and thanks."""
    new = whats_new(pr_text) or default_whats_new(since)
    base = re.sub(r"^## What's new[ \t]*\n(?:- TODO:.*\n?)?", lambda _m: f"{WHATS_NEW}\n{new}\n", base, count=1,
                  flags=re.M)
    win = ["### Windows package (tinglongwu-win64.zip)", "", verification.strip() or "(not built)", "",
           gpu_line(hw_run, head)]
    return "\n".join([base.rstrip(), "", *win, "", changes_md(since, changes), thanks_md(thanks)])


# ---------------------------------------------------------------- gh / git I/O

def gh_json(*args: str):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout or "null")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def git_tags() -> dict[str, str]:
    out = git("for-each-ref", "--format=%(refname:short) %(objectname) %(*objectname)", "refs/tags/v*")
    tags = {}
    for line in out.splitlines():
        name, obj, peeled = (line.split() + [""])[:3]
        tags[name] = peeled or obj  # annotated tags: the commit they point at
    return tags


def repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


def in_head(sha: str) -> bool:
    return bool(sha) and subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=ROOT,
                                        capture_output=True).returncode == 0


def history(rep: str, tag: str):
    since = latest_tag([t for t in git_tags() if t != tag])
    date = git("log", "-1", "--format=%cI", since).strip() if since else ""
    changes = gh_json("pr", "list", "-R", rep, "--state", "merged", "--base", "dev", "-L", "500",
                      *(["--search", f"merged:>{date}"] if date else []), "--json", "number,title,mergeCommit")
    # only PRs inside the released checkout: one merged to dev during the build is not in this release
    changes = [c for c in changes if in_head((c.get("mergeCommit") or {}).get("oid", ""))]
    prs = gh_json("pr", "list", "-R", rep, "--state", "all", "-L", "3000", "--json", "number,author,url")
    issues = gh_json("issue", "list", "-R", rep, "--state", "all", "-L", "3000", "--json", "number,author,url")
    return since, sorted(changes, key=lambda c: c["number"]), contributors(prs, issues, rep.split("/")[0])


def read(path: str | None) -> str:
    return Path(path).read_text("utf-8") if path else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("pr-body")
    b.add_argument("--existing", help="file with the current release PR body (keeps its What's new)")
    b.add_argument("--out", required=True)
    sub.add_parser("plan")
    n = sub.add_parser("notes")
    for k in ("--version", "--base", "--out"):
        n.add_argument(k, required=True)
    n.add_argument("--verification")
    n.add_argument("--pr", default="")
    n.add_argument("--hw-run", default="")
    n.add_argument("--head", default="")
    a = ap.parse_args(argv)
    version = app_version((ROOT / "tinglongwu" / "__init__.py").read_text("utf-8"))
    rep = repo()
    if a.cmd == "plan":
        event, ref_name, sha, ref_type = (os.environ.get(k, "") for k in
                                          ("GITHUB_EVENT_NAME", "GITHUB_REF_NAME", "GITHUB_SHA", "GITHUB_REF_TYPE"))
        pulls = gh_json("api", f"repos/{rep}/commits/{sha}/pulls") if event == "push" and ref_type != "tag" else []
        runs_of = lambda h: gh_json("api", f"repos/{rep}/actions/runs?head_sha={h}&per_page=100")["workflow_runs"]
        def published(tag):
            r = subprocess.run(["gh", "release", "view", tag, "-R", rep, "--json", "isDraft", "-q", ".isDraft"],
                               capture_output=True, text=True)
            return r.returncode == 0 and r.stdout.strip() == "false"

        for k, v in plan(event, ref_name, sha, version, git_tags(), pulls, rep, runs_of, published,
                          ref_type or "branch").items():
            print(f"{k}={v}")
        return 0
    if a.cmd == "pr-body":
        tag = f"v{version}"
        since, changes, thanks = history(rep, tag)
        body = pr_body(version, list(git_tags()), since, changes, thanks, read(a.existing), git("rev-parse", "HEAD"))
        Path(a.out).write_text(body, "utf-8")
        print(f"release: {tag}")
        return 0
    pr_text = gh_json("pr", "view", a.pr, "-R", rep, "--json", "body")["body"] if a.pr else ""
    since, changes, thanks = history(rep, a.version)
    text = release_notes(read(a.base), pr_text, since, read(a.verification), a.hw_run, a.head, changes, thanks)
    Path(a.out).write_text(text, "utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
