#!/usr/bin/env python3
"""Release automation helpers (#414) for release-pr.yml (the release PR to main) and release.yml (build and
publish after that PR merged).

Two lanes (#976, owner 2026-11-15): the release PR is cut from a snapshot branch `release/vX.Y.Z` that points at the
dev commit of the version bump, or the first later one that adds its docs/whats_new.md section (#1012;
`snapshot` below), never from `dev` itself, so merges to dev no longer restart or
freeze a release. The required checks run on the snapshot head: release-pr.yml dispatches e2e / audio-routing /
hw-tests on the snapshot ref (their concurrency groups are per ref, so later dev pushes cannot cancel them); ci and
windows-build come from the dev push of that commit (never cancelled). Transition: the legacy `dev -> main` release
PR (opened by the old release-pr.yml still on main) is accepted everywhere until one release carries the new
workflows to main.

    python scripts/release_notes.py pr-body [--existing FILE] [--snapshot release/vX] --out F   PR title + body
    python scripts/release_notes.py snapshot                           release-pr.yml: cut / dispatch / clean up
    python scripts/release_notes.py plan                               release.yml: publish? tag? (GITHUB_OUTPUT lines)
    python scripts/release_notes.py notes --version vX --base F --verification R [--pr N] [--hw-run URL] --out F
    python scripts/release_notes.py auto                               auto-release.yml: merge the release PR now?
    python scripts/release_notes.py bump-check --pr N                  mergers: may this bump PR skip Opus? (#1267)

Auto-release (#589): auto-release.yml runs `auto` after ci / e2e / windows-build / hw-tests complete on dev. It merges
the open dev -> main release PR only when AUTO_RELEASE is true, the throttle issue is at level >= 1, the PR head is
the run's commit, the title is v<__version__>, newer than every tag, all four checks are green on the head and
docs/whats_new.md has a section for that version (#848: hw-tests that has not started within HW_RUNNER_WAIT_MIN
minutes, runner offline, is deferred: scripts/hw_deferral.py); then it dispatches release.yml on main with publish=true (a merge
by the workflow token fires no `push`), which `plan` treats like the push of the merged release PR.
What's new comes from docs/whats_new.md (`## vX.Y.Z` section, written by the bump PR); a release PR body edit is the
fallback for a hand merge. Without either the notes keep `- TODO:` and windows-installer leaves the release a draft.

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


def _pack_manifest():  # by path: importing the tinglongwu package would install its offline guard in this script
    import importlib.util
    spec = importlib.util.spec_from_file_location("tlwu_pack_manifest", ROOT / "tinglongwu" / "pack_manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_HW = None


def _hw():  # scripts/hw_deferral.py (#848), by path like _pack_manifest
    global _HW
    if _HW is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("tlwu_hw_deferral", Path(__file__).resolve().parent / "hw_deferral.py")
        _HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_HW)
    return _HW


TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
TITLE_RE = re.compile(r"^release:\s*(v\S+)\s*$", re.I)
SNAPSHOT_PREFIX = "release/"   # #976: release/vX.Y.Z, the release PR head
LEGACY_HEAD = "dev"            # #976 transition: the release PR opened by the old release-pr.yml on main
# #976: the required checks that release-pr.yml dispatches on the snapshot ref. ci and windows-build are not
# dispatched: their dev-push runs are never cancelled (ci has no concurrency group, windows-build #888) and a
# windows-build on a release/ ref could not use dev's build cache.
SNAPSHOT_DISPATCH = ("e2e", "audio-routing", "hw-tests", "install-clean")
WORKFLOW_FILES = {"e2e": "e2e.yml", "audio-routing": "audio-routing.yml", "hw-tests": "hw-tests.yml",
                  "install-clean": "install-clean.yml"}
REQUIRED_CHECKS = ("ci", "e2e", "windows-build", "hw-tests", "audio-routing",
                   "install-clean")  # #849: VB-CABLE routing on windows-latest; #1016: clean-Windows speak smoke
WHATS_NEW = "## What's new"
WHATS_NEW_FILE = "docs/whats_new.md"
THROTTLE_RE = re.compile(r"^\s*level:\s*(\d+)\b", re.I)


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


def snapshot_branch(tag: str) -> str:
    return f"{SNAPSHOT_PREFIX}{tag}"


def release_head_ok(pr: dict) -> bool:
    """#976: a release PR head is `release/<the title's version>` (snapshot) or, during the transition, `dev`."""
    ref = (pr.get("head") or {}).get("ref", "")
    v = title_version(pr.get("title", ""))
    return ref == LEGACY_HEAD or (v is not None and ref == snapshot_branch(v))


def is_snapshot_branch(name: str) -> bool:
    """A release/vX.Y.Z branch this lane manages; other release/* names (e.g. release/notes) are never touched."""
    return name.startswith(SNAPSHOT_PREFIX) and TAG_RE.match(name[len(SNAPSHOT_PREFIX):]) is not None


def is_snapshot(pr: dict) -> bool:
    return ((pr.get("head") or {}).get("ref", "")).startswith(SNAPSHOT_PREFIX)


def _release_pr(p: dict, repo: str) -> bool:
    head, base = p.get("head") or {}, p.get("base") or {}
    return (base.get("ref") == "main" and release_head_ok(p) and (head.get("repo") or {}).get("full_name") == repo
            and p.get("title", "").strip().lower().startswith("release:"))


def release_pr_of(pulls: list[dict], repo: str) -> dict | None:
    """The merged release PR (release/vX.Y.Z or legacy dev -> main, from this repo, not a fork) among the PRs of a
    commit."""
    for p in pulls:
        if p.get("merged_at") and _release_pr(p, repo):
            return p
    return None


def _never_started(run: dict, jobs_of) -> bool:
    """#1139: a release PR pull_request run left waiting for approval (`action_required`, #1104) is flipped by GitHub to
    `completed/failure` with zero jobs when the PR merges or closes. It never ran, so it is no evidence either way.
    Only pull_request runs qualify: a push or dispatch run with zero jobs (e.g. a broken workflow file) still blocks."""
    if jobs_of is None or run.get("event") != "pull_request" or run.get("status") != "completed":
        return False
    if run.get("conclusion") not in ("failure", "cancelled", "startup_failure"):
        return False
    return not jobs_of(run.get("id"))


def _counts(run: dict, jobs_of=None) -> bool:
    """#1016: a pull_request run of install-clean skips speak-smoke (job `if`), and a skipped job reports success, so
    it is never evidence for the speak-smoke requirement."""
    if run.get("conclusion") == "action_required":  # #1104: a run waiting for approval never ran: no evidence
        return False
    if _never_started(run, jobs_of):  # #1139: the same run after the PR merged
        return False
    if run.get("event") == "pull_request" and str(run.get("head_branch") or "").startswith("release/"):
        return False  # #1540: the release PR's own pull_request run re-tests the snapshot commit; the dev push and
        # release-pr.yml dispatch runs on that same commit are the evidence, so a queued duplicate never blocks
    return not (run.get("name") == "install-clean" and run.get("event") == "pull_request")


def check_problems(runs: list[dict], required=REQUIRED_CHECKS, jobs_of=None) -> list[str]:
    """Owner gate: the newest run of each required workflow on the commit must have succeeded. A never-started
    `action_required` run (release PR pull_request runs, #1104) is skipped, so it neither passes nor blocks; with
    `jobs_of`, so is that run once GitHub has flipped it to a zero-job failure (#1139)."""
    out = []
    for name in required:
        mine = [r for r in runs if r.get("name") == name and _counts(r, jobs_of)]
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
         runs_of=lambda sha: [], published=lambda tag: False, ref_type: str = "branch",
         publish_input: bool = False, jobs_of=lambda run_id: []) -> dict:
    """publish = tag + Release; build = build and check the Windows zip; head = released dev commit.
    #589: workflow_dispatch on main with publish=true (sent by auto-release.yml after it merged the release PR) is
    treated like the push to main; every other dispatch is a dry run."""
    out = {"publish": "false", "build": "false", "tag": "", "pr": "", "head": "", "hw_run": "", "reason": ""}
    if event == "workflow_dispatch":
        if not (publish_input and ref_type == "branch" and ref_name == "main"):
            why = " (publish=true is honoured only on main)" if publish_input else ""
            out.update(build="true", tag=f"v{version}",
                       reason=f"workflow_dispatch on {ref_name}: dry run, publishes nothing{why}")
            return out
        event = "push"  # merged by auto-release.yml with the workflow token, which fires no push
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
    bad = check_problems(runs, jobs_of=jobs_of)
    hw_mod = _hw()
    # #848: auto-release merged it with hw-tests deferred (runner offline); the cloud checks must still be green
    deferred = bool(bad) and hw_mod.plan_deferred(pr.get("body") or "", runs, jobs_of) and not check_problems(
        runs, hw_mod.CLOUD_CHECKS, jobs_of)
    if bad and not deferred:
        raise SystemExit(f"release PR #{pr['number']}: required checks not green on {head[:12]}: " + "; ".join(bad))
    hw = None if deferred else max((r for r in runs if r.get("name") == "hw-tests"), key=lambda r: r.get("id", 0))
    out.update(publish="true", build="true", tag=tag, pr=str(pr["number"]), head=head,
               hw_run=hw.get("html_url", "") if hw else "",
               reason=f"release PR #{pr['number']} merged" + (f" ({hw_mod.DEFERRED_LINE}, #848)" if deferred else ""))
    if deferred:
        out["hw_deferred"] = "true"
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
    """The `## What's new` section of a release PR body (the owner may edit it), None if absent, still TODO or the
    generated placeholder. It ends at the next generated section, so the owner's own ### subsections are kept."""
    m = re.search(r"^## What's new[ \t]*\n(.*?)(?=^#{1,2} |^### (?:Changes|Thanks)\b|^---|\Z)", body or "", re.M | re.S)
    text = m.group(1).strip() if m else ""
    generated = text.startswith("- Fixes and improvements") and text.endswith("the full list is below.")
    return text if text and "TODO" not in text and not generated else None


def whats_new_section(text: str, tag: str) -> str | None:
    """#589: the `## vX.Y.Z` section of docs/whats_new.md (written by the bump PR); None if absent, empty or TODO."""
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)  # the file's example sits in a comment
    m = re.search(rf"^##[ \t]+{re.escape(tag)}[ \t]*\n(.*?)(?=^##? |\Z)", text, re.M | re.S)
    body = m.group(1).strip() if m else ""
    return body if body and "TODO" not in body else None


def todo_whats_new(tag: str) -> str:
    return f"- TODO: add a `## {tag}` section to {WHATS_NEW_FILE} (the bump PR writes it)."


_N = r"(0|[1-9][0-9]*)"  # canonical ASCII component: app_version keeps the spelling, the tag must match it
VERSION_LINE_RE = re.compile(rf'^__version__[ \t]*=[ \t]*"{_N}\.{_N}\.{_N}"[ \t]*$')  # double quotes, as app_version


def _patch_lines(patch: str) -> tuple[list[str], list[str]]:
    """Removed and added lines of a GitHub `pulls/N/files` patch (hunk headers and context dropped)."""
    removed, added = [], []
    for line in (patch or "").split("\n"):  # physical LF lines only: splitlines() would also cut at \f, \v, \x1c...
        if line.startswith("@@") or line.startswith("\\"):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


def bump_exempt(files: list[dict], whats_new_text: str | None = None) -> tuple[bool, str]:
    """#1267: a bump PR skips Opus only when its diff is the `__version__` patch bump in tinglongwu/__init__.py plus,
    optionally, one new filled `## vX.Y.Z` section (same version) added to docs/whats_new.md and nothing else.
    `files` is the `pulls/N/files` list (filename, status, patch); `whats_new_text` is docs/whats_new.md at the PR head
    (needed when the PR touches it: the section must be the one the release reads, not hidden in a comment).
    Returns (exempt, reason)."""
    by = {f.get("filename"): f for f in files}
    other = sorted(set(by) - {"tinglongwu/__init__.py", WHATS_NEW_FILE})
    if other:
        return False, f"other files: {', '.join(other)}"
    init = by.get("tinglongwu/__init__.py")
    if not init or init.get("status") != "modified":
        return False, "no __version__ change"
    removed, added = (_patch_lines(init.get("patch", "")))
    old = [VERSION_LINE_RE.match(x) for x in removed]
    new = [VERSION_LINE_RE.match(x) for x in added]
    if len(removed) != 1 or len(added) != 1 or not old[0] or not new[0]:
        return False, "tinglongwu/__init__.py changes more than the __version__ line"
    o, n = tuple(map(int, old[0].groups())), tuple(map(int, new[0].groups()))
    if n != (o[0], o[1], o[2] + 1):
        return False, f"not a patch bump: {'.'.join(map(str, o))} -> {'.'.join(map(str, n))}"
    tag = "v" + ".".join(map(str, n))
    wn = by.get(WHATS_NEW_FILE)
    if wn is None:
        return True, f"version line only ({tag})"
    if wn.get("status") != "modified":
        return False, f"{WHATS_NEW_FILE} is {wn.get('status')}, not modified"
    removed, added = _patch_lines(wn.get("patch", ""))
    if removed:
        return False, f"{WHATS_NEW_FILE} removes or edits existing lines"
    runs, prev = 0, False  # the new section must be one contiguous block of added lines that starts at its heading
    for line in (wn.get("patch") or "").split("\n"):
        cur = line.startswith("+")
        runs += cur and not prev
        prev = cur
    if runs != 1 or next((x for x in added if x.strip()), "") != f"## {tag}":
        return False, f"{WHATS_NEW_FILE} must add one block that starts with `## {tag}`"
    heads = [x for x in added if re.match(r"^ {0,3}#{1,6}(?:[ \t]|$)", x)]  # any GFM ATX heading, up to 3 spaces in
    if heads != [f"## {tag}"]:
        return False, f"{WHATS_NEW_FILE} must add exactly one `## {tag}` heading"
    body = [x for x in added[added.index(f"## {tag}") + 1:] if x.strip()]
    # plain one-level bullets only: no Setext/ATX heading (also not inside an item), quote, nested list, HTML or prose
    if not all(re.match(r"^- (?![#>*+=<-]|[0-9]+[.)](?:\s|$))\S", x) for x in body):
        return False, f"`## {tag}` section may only hold `- ` bullet lines"
    if any(c != "\t" and (ord(c) < 32 or ord(c) == 127) for x in added for c in x):
        return False, f"{WHATS_NEW_FILE} adds control characters"
    section = whats_new_section("\n".join(added) + "\n", tag)
    if section is None:
        return False, f"`## {tag}` section is empty or TODO"
    if whats_new_text is None:
        return False, f"{WHATS_NEW_FILE} at the PR head not read"
    if any("<!--" in x or "-->" in x for x in added):
        return False, f"{WHATS_NEW_FILE} section may not open or close an HTML comment"
    # Visibility, checked directly: the head document minus the added block (= the base) must have no readable
    # `## tag` section, and the head document must read exactly the added one. Then the added block is the section
    # the release reads, wherever comments or odd heading spellings sit in the rest of the file.
    lines, n = whats_new_text.split("\n"), len(added)
    at = [i for i in range(len(lines) - n + 1) if lines[i:i + n] == added]
    if len(at) != 1:
        return False, f"added block not found exactly once in {WHATS_NEW_FILE} at the PR head"
    base = "\n".join(lines[:at[0]] + lines[at[0] + n:])
    if whats_new_section(base, tag) is not None:
        return False, f"{WHATS_NEW_FILE} already has a readable `## {tag}` section"
    if whats_new_section(whats_new_text, tag) != section:
        return False, f"`## {tag}` is not the section the release reads from {WHATS_NEW_FILE} (inside a comment?)"
    return True, f"version line + {WHATS_NEW_FILE} `## {tag}` section"


def bump_check_pr(head_of, files_of, text_at) -> tuple[bool, str, str]:
    """#1267: bump_exempt on one PR revision. The head SHA is read before the file list and again after the
    whats_new read; a push in between refuses the exemption. Returns (exempt, reason, checked head SHA); mergers merge
    with that SHA as the expected head."""
    head = head_of()
    files = files_of()
    text = text_at(head) if any(f.get("filename") == WHATS_NEW_FILE for f in files) else None
    if head_of() != head:
        return False, "PR head moved during the check; run it again", head
    ok, why = bump_exempt(files, text)
    return ok, why, head


def throttle_level(body: str | None) -> int | None:
    """First line of the throttle issue (#574): `level: N`. None when missing or unreadable."""
    m = THROTTLE_RE.match((body or "").split("\n", 1)[0])
    return int(m.group(1)) if m else None


def open_release_pr(pulls: list[dict], repo: str) -> dict | None:
    """The open `release:` PR to main from this repo (not a fork): a release/vX.Y.Z snapshot first, else the legacy
    dev -> main PR (#976 transition)."""
    mine = [p for p in pulls if p.get("state", "open") == "open" and _release_pr(p, repo)]
    return next((p for p in mine if is_snapshot(p)), None) or (mine[0] if mine else None)


def release_commit(tag: str, commits: list[str], show) -> str | None:
    """#1012: the first of `commits` (the bump commit, then the later dev commits, oldest first) whose
    tinglongwu/__init__.py says `tag` AND whose docs/whats_new.md has a filled `## tag` section; None if none has yet.
    A bump PR without notes (v0.3.6: #970, notes in #988) must not be snapshotted before its notes land, or auto_plan
    (which reads whats_new at the snapshot head) would stall for good."""
    for sha in commits:
        init = show(sha, "tinglongwu/__init__.py")
        if init is None or f"v{app_version(init)}" != tag:
            continue
        if whats_new_section(show(sha, WHATS_NEW_FILE) or "", tag) is not None:
            return sha
    return None


def snapshot_plan(version: str, tags: dict[str, str], bump_sha: str, ahead: int, branches: dict[str, str],
                  pulls: list[dict], repo: str, runs_of=lambda sha: [], cut_sha=None) -> dict:
    """#976 release-pr.yml: what to do for dev's __version__ (pure; release-pr.yml acts on the result).
    branches = {release/vX: sha} on origin; pulls = open PRs into main; bump_sha = the dev commit that set __version__;
    ahead = commits that bump_sha has and main lacks. cut_sha (#1012) = release_commit(): the commit a new snapshot is
    cut at (the bump, or the later commit that added its whats_new section); "" = no such commit yet (wait, cut
    nothing); None = bump_sha.
    Out: cut (create release/v<version> at bump_sha), pr (open the release PR: none open yet for the snapshot),
    dispatch (required workflows to run on the snapshot ref, with the PR), close (superseded release PR numbers),
    delete (snapshot branches to delete: tag exists or superseded, no open PR left)."""
    tag = f"v{version}"
    want = snapshot_branch(tag)
    out = {"cut": False, "pr": False, "dispatch": [], "close": [], "delete": [], "branch": want, "sha": "",
           "tag": tag, "reason": ""}
    rel = [p for p in pulls if p.get("state", "open") == "open" and _release_pr(p, repo)]
    releasable = parse_tag(tag) is not None and tag not in tags and ahead > 0
    last = latest_tag(list(tags))
    if releasable and last and parse_tag(tag) <= parse_tag(last):
        releasable = False
    keep = want if releasable else None
    for p in rel:  # a release PR for any other version (or a dev -> main legacy one once a snapshot exists) goes
        ref = p["head"]["ref"]
        if ref != keep and (is_snapshot(p) or keep):
            out["close"].append(p["number"])
    closing = {p["head"]["ref"] for p in rel if p["number"] in out["close"]}
    still_open = {p["head"]["ref"] for p in rel} - closing
    for name in sorted(branches):
        if name != keep and name not in still_open and is_snapshot_branch(name):
            out["delete"].append(name)
    if not releasable:
        why = ("not a release version" if parse_tag(tag) is None else
               f"{tag} is already released (tag exists); waiting for the patch bump (#785)" if tag in tags else
               f"{tag} is not newer than the latest tag {last}" if last and parse_tag(tag) <= parse_tag(last) else
               "the bump commit is already on main")
        out["reason"] = f"no snapshot: {why}"
        return out
    sha = branches.get(want) or (bump_sha if cut_sha is None else cut_sha)
    if not sha:
        out["reason"] = (f"no snapshot: waiting for whats_new section `## {tag}` in {WHATS_NEW_FILE} on dev "
                         "(#1012: the snapshot is cut at the first commit that has it)")
        return out
    out["sha"] = sha
    if want not in branches:
        out["cut"] = True
    if want not in still_open:
        out["pr"] = True
        runs = runs_of(sha)
        for name in SNAPSHOT_DISPATCH:
            mine = [r for r in runs if r.get("name") == name and _counts(r)]
            r = max(mine, key=lambda r: r.get("id", 0)) if mine else {}
            done = r.get("status") == "completed" and r.get("conclusion") == "success"
            # an in-progress hw-tests run is never cancelled (cancel-in-progress: false); e2e / audio-routing on dev are
            running_hw = name == "hw-tests" and r.get("status") == "in_progress"
            if not (done or running_hw):
                out["dispatch"].append(name)
    out["reason"] = (f"{'cut ' + want + ' at ' + sha[:12] if out['cut'] else want + ' exists at ' + sha[:12]}; "
                     f"{'open the release PR' if out['pr'] else 'release PR already open'}")
    return out


def auto_plan(enabled: str, throttle: str | None, run_sha: str, version: str, tags: dict[str, str],
              pulls: list[dict], repo: str, runs_of, whats_new_text: str, merged: list[dict] = (),
              jobs_of=lambda run_id: [], now=None, show=lambda sha, path: None, in_dev=lambda sha: False) -> dict:
    """#589 auto-release gate: merge = merge the release PR at `head` now, then dispatch release.yml on main.
    #848: when hw-tests on the head has not started within HW_RUNNER_WAIT_MIN, it is deferred (hw_deferred=true):
    ci, e2e and windows-build suffice and the workflow marks the PR body before merging.
    #976: for a release/vX.Y.Z snapshot PR the version and docs/whats_new.md are read at the snapshot head
    (show(sha, path) -> text or None), the head must be a dev commit (in_dev), and run_sha does not matter: any
    completed check or the schedule re-evaluates the snapshot. The legacy dev -> main PR keeps head == run_sha."""
    out = {"merge": "false", "pr": "", "head": "", "tag": "", "reason": ""}

    def stop(why):
        out["reason"] = why
        return out
    if enabled != "true":
        return stop("kill switch: repo variable AUTO_RELEASE is not 'true'")
    level = throttle_level(throttle)
    if level is None or level < 1:
        return stop(f"kill switch: throttle level is {'unreadable' if level is None else level} (needs >= 1)")
    pr = open_release_pr(pulls, repo)
    if pr is None:
        return stop("no open dev -> main or release/vX.Y.Z -> main 'release:' PR")
    head = (pr.get("head") or {}).get("sha", "")
    out.update(pr=str(pr["number"]), head=head)
    if is_snapshot(pr):
        if not in_dev(head):
            return stop(f"snapshot {pr['head']['ref']} head {head[:12]} is not a dev commit")
        init = show(head, "tinglongwu/__init__.py")
        if init is None:
            return stop(f"cannot read tinglongwu/__init__.py at the snapshot head {head[:12]}")
        snap_version = app_version(init)
        if snap_version != version:  # bump reverted or superseded on dev: never release the old snapshot
            return stop(f"snapshot {pr['head']['ref']} is v{snap_version} but dev's __version__ is v{version}; "
                        "release-pr.yml closes it")
        whats_new_text = show(head, WHATS_NEW_FILE) or ""
    elif head != run_sha:
        return stop(f"release PR #{pr['number']} head {head[:12]} is not this run's commit {run_sha[:12]}")
    tag = f"v{version}"
    if title_version(pr.get("title", "")) != tag:
        return stop(f"release PR title {pr.get('title')!r} does not name {tag} (= __version__)")
    last = latest_tag(list(tags))
    if tag in tags or (last and parse_tag(tag) <= parse_tag(last)):
        return stop(f"{tag} is not newer than the latest tag {last}: bump __version__ first")
    done = release_pr_of([p for p in merged if title_version(p.get("title", "")) == tag], repo)
    if done is not None:  # merged, release.yml has not tagged yet: never merge the same version twice
        return stop(f"release PR #{done['number']} for {tag} is already merged: bump __version__ first")
    runs = runs_of(head)
    hw_mod = _hw()
    hw = hw_mod.decide(runs, jobs_of, now)
    bad = check_problems(runs, hw_mod.required_checks(hw["state"], REQUIRED_CHECKS), jobs_of)
    if bad:
        if hw["state"] == "waiting":
            bad.append(hw["reason"])
        return stop("required checks not green on the head: " + "; ".join(bad))
    if whats_new_section(whats_new_text, tag) is None:
        return stop(f"{WHATS_NEW_FILE} has no filled `## {tag}` section")
    if hw["state"] == "deferred":
        out.update(merge="true", tag=tag, hw_deferred="true",
                   reason=f"release PR #{pr['number']} is green for {tag} on {', '.join(hw_mod.CLOUD_CHECKS)}; "
                          f"{hw['reason']}: merge and publish")
        return out
    out.update(merge="true", tag=tag, reason=f"release PR #{pr['number']} is green for {tag}: merge and publish")
    return out


def pr_body(version: str, tags: list[str], since: str | None, changes: list[dict], thanks: dict,
            existing: str = "", dev_sha: str = "", whats_new_text: str = "", snapshot: str = "") -> str:
    tag = f"v{version}"
    lines = []
    if tag in tags:
        lines += [f"> **Not releasable yet:** tag `{tag}` already exists. Bump `__version__` in "
                  "`tinglongwu/__init__.py` on dev; this PR then updates itself.", ""]
    new = whats_new_section(whats_new_text, tag) or whats_new(existing) or todo_whats_new(tag)
    lines += [WHATS_NEW, new, "", changes_md(since, changes),
              thanks_md(thanks), "---",
              f"This PR releases `{tag}` (= `tinglongwu.__version__`). `auto-release.yml` merges it once "
              f"{', '.join(REQUIRED_CHECKS)} are green on its head commit and `{WHATS_NEW_FILE}` has a `## {tag}` "
              "section (What's new above comes from there); the `release` workflow then checks the same gates, "
              "builds the Windows zip on windows-latest, runs check_dist_dlls, the llama CPU-instruction check, "
              "`--smoke` and `--selftest`, tags `main` and publishes the GitHub Release with the setup wizard.", "",
              (f"Generated by release-pr.yml (#414) from the snapshot `{snapshot}` = dev {dev_sha[:7]} (#976: merges "
               "to dev do not change this PR; the next version gets its own snapshot)." if snapshot else
               f"Generated by release-pr.yml (#414) from dev {dev_sha[:7]}.")]
    return "\n".join(lines) + "\n"


# #762: the release zip is the slim core (build_windows.py --core): CPU llama, no CUDA libraries inside
CORE_ZIP_NOTE = ("The zip below is the slim core: it has no CUDA libraries inside, was built on a GPU-less CI runner "
                 "and tested there on the CPU path only.")


def gpu_line(hw_run: str, head: str, hw_deferred: bool = False) -> str:
    if hw_deferred:  # #848: one line that says so
        hw_mod = _hw()
        return (f"**GPU: {hw_mod.DEFERRED_LINE}.** The maintainer's self-hosted hw-tests runner did not start within "
                f"{hw_mod.HW_RUNNER_WAIT_MIN} minutes, so this release shipped on ci, e2e and windows-build; hw-tests "
                "runs later on this commit and a failure opens a P0 issue.")
    if hw_run:
        return (f"**GPU:** validated by [hw-tests]({hw_run}) on the maintainer's self-hosted NVIDIA runner for commit "
                f"`{head[:12]}`. {CORE_ZIP_NOTE}")
    return f"**GPU: not validated.** The CI runner has no GPU. {CORE_ZIP_NOTE}"


def release_notes(base: str, pr_text: str, since: str | None, verification: str, hw_run: str, head: str,
                  changes: list[dict], thanks: dict, pack_manifest: dict | None = None, whats_new_text: str = "",
                  tag: str = "", hw_deferred: bool = False) -> str:
    """render_support.py --notes output with What's new filled in, plus the Windows checks, changes and thanks.
    What's new: docs/whats_new.md `## <tag>`, else the release PR's edited section; else the TODO line stays, so
    windows-installer keeps the release a draft (#589: the no-TODO gate)."""
    new = whats_new_section(whats_new_text, tag) or whats_new(pr_text)
    if new:
        base = re.sub(r"^## What's new[ \t]*\n(?:- TODO:.*\n?)?", lambda _m: f"{WHATS_NEW}\n{new}\n", base, count=1,
                      flags=re.M)
    win = ["### Windows package (tinglongwu-win64.zip)", "", verification.strip() or "(not built)", "",
           gpu_line(hw_run, head, hw_deferred)]
    packs = [_pack_manifest().sizes_md(pack_manifest), ""] if pack_manifest else []  # #615: every optional pack with its size
    return "\n".join([base.rstrip(), "", *win, "", *packs, changes_md(since, changes), thanks_md(thanks)])


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


def git_show(sha: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return r.stdout if r.returncode == 0 else None


def repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]


def in_tip(sha: str, tip: str) -> bool:
    return bool(sha) and subprocess.run(["git", "merge-base", "--is-ancestor", sha, tip or "HEAD"], cwd=ROOT,
                                        capture_output=True).returncode == 0


def history(rep: str, tag: str, tip: str = "HEAD"):
    """tip: the released dev commit (the release PR head), so a squash/rebase merge to main still finds its PRs."""
    since = latest_tag([t for t in git_tags() if t != tag])
    date = git("log", "-1", "--format=%cI", since).strip() if since else ""
    changes = gh_json("pr", "list", "-R", rep, "--state", "merged", "--base", "dev", "-L", "500",
                      *(["--search", f"merged:>{date}"] if date else []), "--json", "number,title,mergeCommit")
    # only PRs inside the released dev commit: one merged to dev during the build is not in this release
    if subprocess.run(["git", "cat-file", "-e", f"{tip}^{{commit}}"], cwd=ROOT, capture_output=True).returncode == 0:
        changes = [c for c in changes if in_tip((c.get("mergeCommit") or {}).get("oid", ""), tip)]
    prs = gh_json("pr", "list", "-R", rep, "--state", "all", "-L", "3000", "--json", "number,author,url")
    issues = gh_json("issue", "list", "-R", rep, "--state", "all", "-L", "3000", "--json", "number,author,url")
    return since, sorted(changes, key=lambda c: c["number"]), contributors(prs, issues, rep.split("/")[0])


def git_ls_remote_heads(pattern: str) -> dict[str, str]:
    """{branch: sha} of origin heads matching pattern (e.g. release/*)."""
    out = git("ls-remote", "--heads", "origin", pattern)
    heads = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return heads


def snapshot_cli(version: str, rep: str) -> int:
    """#976 release-pr.yml: print the snapshot_plan for HEAD (a dev commit; origin/main fetched) as GITHUB_OUTPUT lines."""
    bump = git("log", "-1", "--format=%H", "-G", r"^__version__\s*=", "HEAD", "--", "tinglongwu/__init__.py").strip()
    bump = bump or git("rev-parse", "HEAD").strip()
    ahead = int(git("rev-list", "--count", f"origin/main..{bump}").strip() or 0)
    later = git("rev-list", "--reverse", "--first-parent", f"{bump}..HEAD").split()
    cut = release_commit(f"v{version}", [bump] + later, git_show) or ""
    pulls = gh_json("api", f"repos/{rep}/pulls?state=open&base=main&per_page=100")
    runs_of = lambda h: gh_json("api", f"repos/{rep}/actions/runs?head_sha={h}&per_page=100")["workflow_runs"]
    out = snapshot_plan(version, git_tags(), bump, ahead, git_ls_remote_heads(f"{SNAPSHOT_PREFIX}*"), pulls, rep,
                        runs_of, cut_sha=cut)
    for k, v in out.items():
        v = " ".join(str(x) for x in v) if isinstance(v, list) else ("true" if v is True else "false" if v is False else v)
        print(f"{k}={v}")
    return 0


def read(path: str | None) -> str:
    return Path(path).read_text("utf-8") if path else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("pr-body")
    b.add_argument("--existing", help="file with the current release PR body (keeps its What's new)")
    b.add_argument("--out", required=True)
    b.add_argument("--snapshot", default="", help="#976: the release/vX.Y.Z branch this body is for (HEAD = its commit)")
    sub.add_parser("plan")
    sub.add_parser("snapshot")
    sub.add_parser("auto")
    bc = sub.add_parser("bump-check", help="#1267: may this bump PR merge without an Opus PASS?")
    bc.add_argument("--pr", required=True)
    bc.add_argument("--files", default="", help="JSON of pulls/N/files (default: fetched with gh)")
    bc.add_argument("--whats-new", default="", help="docs/whats_new.md at the PR head (default: fetched with gh)")
    n = sub.add_parser("notes")
    for k in ("--version", "--base", "--out"):
        n.add_argument(k, required=True)
    n.add_argument("--verification")
    n.add_argument("--pr", default="")
    n.add_argument("--hw-run", default="")
    n.add_argument("--head", default="")
    n.add_argument("--packs-manifest", default="", help="pack manifest JSON (tinglongwu/pack_manifest.py)")
    a = ap.parse_args(argv)
    if a.cmd == "bump-check":
        if a.files:
            ok, why = bump_exempt(json.loads(read(a.files)), read(a.whats_new) if a.whats_new else None)
            sha = ""
        else:
            rep = repo()

            def text_at(sha):
                r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github.raw",
                                    f"repos/{rep}/contents/{WHATS_NEW_FILE}?ref={sha}"],
                                   capture_output=True, text=True, encoding="utf-8")
                return r.stdout if r.returncode == 0 else None
            ok, why, sha = bump_check_pr(lambda: gh_json("api", f"repos/{rep}/pulls/{a.pr}")["head"]["sha"],
                                         lambda: gh_json("api", f"repos/{rep}/pulls/{a.pr}/files?per_page=100"),
                                         text_at)
        print(f"bump-exempt: {'yes' if ok else 'no'} ({why})" + (f" at {sha}" if sha else ""))
        return 0 if ok else 1
    version = app_version((ROOT / "tinglongwu" / "__init__.py").read_text("utf-8"))
    rep = repo()
    if a.cmd == "plan":
        event, ref_name, sha, ref_type = (os.environ.get(k, "") for k in
                                          ("GITHUB_EVENT_NAME", "GITHUB_REF_NAME", "GITHUB_SHA", "GITHUB_REF_TYPE"))
        pulls = gh_json("api", f"repos/{rep}/commits/{sha}/pulls") if event == "push" and ref_type != "tag" else []
        runs_of = lambda h: gh_json("api", f"repos/{rep}/actions/runs?head_sha={h}&per_page=100")["workflow_runs"]
        jobs_of = lambda rid: gh_json("api", f"repos/{rep}/actions/runs/{rid}/jobs")["jobs"]
        def published(tag):
            r = subprocess.run(["gh", "release", "view", tag, "-R", rep, "--json", "isDraft", "-q", ".isDraft"],
                               capture_output=True, text=True)
            return r.returncode == 0 and r.stdout.strip() == "false"

        publish_input = os.environ.get("INPUT_PUBLISH", "").lower() == "true"
        if event == "workflow_dispatch" and publish_input and ref_type == "branch" and ref_name == "main":
            pulls = gh_json("api", f"repos/{rep}/commits/{sha}/pulls")
        for k, v in plan(event, ref_name, sha, version, git_tags(), pulls, rep, runs_of, published,
                          ref_type or "branch", publish_input, jobs_of).items():
            print(f"{k}={v}")
        return 0
    if a.cmd == "auto":
        issue = os.environ.get("THROTTLE_ISSUE", "574")
        r = subprocess.run(["gh", "api", f"repos/{rep}/issues/{issue}", "--jq", ".body"], capture_output=True,
                           text=True, encoding="utf-8")
        throttle = r.stdout if r.returncode == 0 else None
        pulls = gh_json("api", f"repos/{rep}/pulls?state=open&base=main&per_page=100")
        merged = gh_json("api", f"repos/{rep}/pulls?state=closed&base=main&per_page=100")
        runs_of = lambda h: gh_json("api", f"repos/{rep}/actions/runs?head_sha={h}&per_page=100")["workflow_runs"]
        jobs_of = lambda rid: gh_json("api", f"repos/{rep}/actions/runs/{rid}/jobs")["jobs"]
        wn = ROOT / WHATS_NEW_FILE
        for k, v in auto_plan(os.environ.get("AUTO_RELEASE", ""), throttle, os.environ.get("RUN_SHA", ""), version,
                              git_tags(), pulls, rep, runs_of, wn.read_text("utf-8") if wn.exists() else "",
                              merged, jobs_of, show=git_show, in_dev=lambda sha: in_tip(sha, "HEAD")).items():
            print(f"{k}={v}")
        return 0
    if a.cmd == "snapshot":
        return snapshot_cli(version, rep)
    if a.cmd == "pr-body":
        tag = f"v{version}"
        if not a.snapshot and git_ls_remote_heads(snapshot_branch(tag)):
            # #976 transition: the old release-pr.yml on main must not open a dev -> main PR next to a snapshot
            raise SystemExit(f"{snapshot_branch(tag)} exists: the snapshot lane releases {tag}, not a dev -> main PR")
        since, changes, thanks = history(rep, tag)
        wn = ROOT / WHATS_NEW_FILE
        body = pr_body(version, list(git_tags()), since, changes, thanks, read(a.existing), git("rev-parse", "HEAD"),
                       wn.read_text("utf-8") if wn.exists() else "", a.snapshot)
        Path(a.out).write_text(body, "utf-8")
        print(f"release: {tag}")
        return 0
    pr_text = gh_json("pr", "view", a.pr, "-R", rep, "--json", "body")["body"] if a.pr else ""
    since, changes, thanks = history(rep, a.version, a.head or "HEAD")
    wn = ROOT / WHATS_NEW_FILE
    text = release_notes(read(a.base), pr_text, since, read(a.verification), a.hw_run, a.head, changes, thanks,
                         _pack_manifest().load_manifest(a.packs_manifest) if a.packs_manifest else None,
                         wn.read_text("utf-8") if wn.exists() else "", a.version,
                         os.environ.get("HW_DEFERRED", "") == "true")  # #848: release.yml passes plan's hw_deferred
    Path(a.out).write_text(text, "utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
