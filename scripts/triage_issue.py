#!/usr/bin/env python3
"""Rule-based triage for a newly opened issue (no AI). Rules: .agent/tlwu_rules.md sections 10, 12, 15.

Run by .github/workflows/triage.yml on `issues.opened`. It only fills labels that are missing and can be
decided by fixed rules. Anything it cannot decide gets `needs-triage` plus one comment listing why; the
patrol (管家) finishes those by hand. Issue text is data: it is only matched against fixed patterns.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
NEEDS_TRIAGE = "needs-triage"
NEEDS_TRIAGE_COLOR = "fbca04"

# section 10: exactly one type label
TYPES = ("bug", "enhancement", "eval", "documentation", "maint")
PRIORITIES = ("P0", "P1", "P2", "P3", "P4")
MODELS = ("model:sonnet", "model:opus")
DEFAULT_MODEL = "model:sonnet"  # opus only after a sonnet failure or by an Opus decision (routine B2 rule)

# title prefix -> labels it decides. "[dev]" is the section 17 "[dev] <check> failing on dev" issue.
PREFIX_LABELS: dict[str, tuple[str, ...]] = {
    "bug": ("bug",),
    "feat": ("enhancement",),
    "idea": ("enhancement",),
    "docs": ("documentation",),
    "eval": ("eval",),
    "test": ("maint",),
    "maint": ("maint",),
    "chore": ("maint",),
    "refactor": ("maint",),
    "ci": ("maint", "area:ci"),
    "release": ("maint", "area:release"),
    "dev": ("bug", "P1", "area:ci"),
}

# section 12: mode that an area alone decides. Areas not listed (gui, audio, cli, mobile, ...) need a human.
AREA_MODE = {
    "area:ci": "mode:shared",
    "area:docs": "mode:shared",
    "area:install": "mode:shared",
    "area:release": "mode:shared",
    "area:eval": "mode:shared",
    "area:netguard": "mode:shared",
    "area:asr": "mode:listen",
    "area:mt": "mode:listen",
    "area:translate": "mode:listen",
    "area:tts": "mode:speak",
    "area:speak": "mode:speak",
}

# section 15: required body lines
FORMAT_FIELDS = ("Why", "Goal", "Acceptance", "Verify")
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
BLOCKING_STATUS = ("status:",)
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
PREFIX = re.compile(r"^\s*\[([A-Za-z]+)\]")


def _field_present(body: str, name: str) -> bool:
    # "Why:", "**Why:**", "### Why", "- Acceptance:" all count
    pat = rf"^[\s>*#\-]*\**{name}\**\s*(:|$)"
    return re.search(pat, body, re.IGNORECASE | re.MULTILINE) is not None


def decide(title: str, body: str, labels: list[str], author_association: str) -> dict:
    """Return {"add": [labels], "reasons": [why needs-triage]} for one new issue."""
    have = set(labels)
    add: list[str] = []
    reasons: list[str] = []

    def put(label: str) -> None:
        if label not in have and label not in add:
            add.append(label)

    def present(group) -> list[str]:
        return [x for x in group if x in have or x in add]

    m = PREFIX.match(title or "")
    hinted = PREFIX_LABELS.get(m.group(1).lower(), ()) if m else ()
    for label in hinted:
        group = TYPES if label in TYPES else PRIORITIES if label in PRIORITIES else None
        if group is not None and present(group):
            continue  # an explicit label wins over the prefix
        if label.startswith("area:") and any(x.startswith("area:") for x in have):
            continue
        put(label)

    types = present(TYPES)
    if not types:
        reasons.append("no type label (bug/enhancement/eval/documentation/maint) and no known title prefix")
    elif len(types) > 1:
        reasons.append("more than one type label: " + ", ".join(types))

    prios = present(PRIORITIES)
    if not prios:
        reasons.append("no P0..P3 label")
    elif len(prios) > 1:
        reasons.append("more than one P label: " + ", ".join(prios))

    if not present(MODELS):
        put(DEFAULT_MODEL)

    areas = sorted({x for x in have | set(add) if x.startswith("area:")})
    if not areas:
        reasons.append("no area:* label")
    elif len(areas) > 1:
        reasons.append("needs exactly one area:* label, has " + ", ".join(areas))

    if not any(x.startswith("mode:") for x in have | set(add)):
        mode = AREA_MODE.get(areas[0]) if len(areas) == 1 else None
        if mode:
            put(mode)
        else:
            reasons.append("no mode:* label and the area does not decide it")

    if not any(x.startswith("src:") for x in have):
        if author_association == "OWNER":
            put("src:owner")
        elif author_association not in TRUSTED:
            put("src:user")

    missing = [f for f in FORMAT_FIELDS if not _field_present(body or "", f)]
    if missing:
        reasons.append("body lacks " + ", ".join(f + ":" for f in missing))
    if CJK.search(title or ""):
        reasons.append("title is not English")

    if author_association not in TRUSTED:
        reasons.append("outside author: needs a collaborator or Opus check before ok-to-work")

    has_status = any(x.startswith(BLOCKING_STATUS) for x in have)
    if reasons:
        put(NEEDS_TRIAGE)
    elif not has_status and not any(x.startswith("needs-") for x in have):
        if "enhancement" in present(TYPES) and "src:ai" in have:
            put("status:proposal")  # AI ideas wait for the owner
        else:
            put("status:todo")
            put("ok-to-work")
    return {"add": add, "reasons": reasons}


def comment_text(reasons: list[str]) -> str:
    return "needs-triage (auto triage): " + "; ".join(reasons) + "."


def _api(method: str, path: str, data=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
    req.add_header("Accept", "application/vnd.github+json")
    payload = None
    if data is not None:
        payload = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, payload, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def main() -> int:
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    issue = event["issue"]
    if "pull_request" in issue:
        return 0
    repo = os.environ["GITHUB_REPOSITORY"]
    n = issue["number"]
    # re-read: labels set right after opening are not in the event payload
    issue = _api("GET", f"/repos/{repo}/issues/{n}")
    if issue.get("state") != "open":
        return 0
    labels = [x["name"] for x in issue.get("labels", [])]
    result = decide(issue.get("title", ""), issue.get("body") or "", labels, issue.get("author_association", ""))
    print(json.dumps({"issue": n, **result}))
    if NEEDS_TRIAGE in result["add"]:
        try:
            _api("POST", f"/repos/{repo}/labels", {"name": NEEDS_TRIAGE, "color": NEEDS_TRIAGE_COLOR,
                                                    "description": "auto triage could not decide; patrol finishes it"})
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 422 = already exists
                raise
    if result["add"]:
        _api("POST", f"/repos/{repo}/issues/{n}/labels", {"labels": result["add"]})
    if result["reasons"]:
        _api("POST", f"/repos/{repo}/issues/{n}/comments", {"body": comment_text(result["reasons"])})
    return 0


if __name__ == "__main__":
    sys.exit(main())
