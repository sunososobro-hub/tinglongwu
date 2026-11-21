"""#1474: weekly dependency audit. Turns `pip-audit -f json` output into at most one open `[deps] vulnerable packages` issue.

Usage: python scripts/deps_audit.py AUDIT.json [--apply]   (needs `gh` with GH_TOKEN for --apply; default prints the decision)
No CVE -> posts nothing. CVE found -> updates the open issue's body if one exists, else creates one (never two).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

REPO = "sunososobro-hub/tinglongwu"
TITLE = "[deps] vulnerable packages"
LABELS = ["bug", "P1", "model:sonnet", "src:ai", "ok-to-work", "status:todo", "area:ci", "mode:shared"]


def find_vulns(report: dict) -> list[tuple[str, str, str, str]]:
    """(package, version, vuln id, fix versions) for every vulnerability in a pip-audit JSON report."""
    out = []
    for dep in report.get("dependencies", []):
        for v in dep.get("vulns", []):
            out.append((dep["name"], dep.get("version", "?"), v["id"], ",".join(v.get("fix_versions", [])) or "none"))
    return sorted(out)


def build_body(vulns: list[tuple[str, str, str, str]]) -> str:
    rows = "\n".join(f"- {n} {ver}: {vid} (fix: {fix})" for n, ver, vid, fix in vulns)
    return (
        "Found by the weekly dependency audit Action.\n\n"
        "Goal: no known-vulnerable pins in requirements/requirements.lock.txt.\n"
        "Acceptance: `pip-audit -r requirements/requirements.lock.txt` reports no vulnerabilities.\n"
        "Out of scope: packages not pinned in requirements/requirements.lock.txt.\n"
        "Verify: `pip-audit --require-hashes -r requirements/requirements.lock.txt`\n\n" + rows + "\n"
    )


def decide(report: dict, open_issue_number: int | None) -> tuple[str, str | None]:
    """('none'|'create'|'update', body)."""
    vulns = find_vulns(report)
    if not vulns:
        return "none", None
    return ("update" if open_issue_number else "create"), build_body(vulns)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def open_issue() -> int | None:
    data = json.loads(_gh("api", f"repos/{REPO}/issues?state=open&per_page=100"))
    for i in data:
        if "pull_request" not in i and i["title"] == TITLE:
            return i["number"]
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    with open(a.report, encoding="utf-8") as f:
        report = json.load(f)
    num = open_issue() if a.apply else None
    action, body = decide(report, num)
    print(f"decision: {action}")
    if action == "none" or not a.apply:
        if body:
            print(body)
        return 0
    if action == "update":
        _gh("api", "-X", "PATCH", f"repos/{REPO}/issues/{num}", "-f", f"body={body}")
    else:
        args = ["api", "-X", "POST", f"repos/{REPO}/issues", "-f", f"title={TITLE}", "-f", f"body={body}"]
        for lb in LABELS:
            args += ["-f", f"labels[]={lb}"]
        _gh(*args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
