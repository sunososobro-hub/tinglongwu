"""#1547: a pull_request into dev must not start a job on a Windows / macOS runner."""
import fnmatch
from pathlib import Path

import pytest
import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"
EXPENSIVE = ("windows", "macos")


def _on(doc):
    return doc.get("on", doc.get(True)) or {}


def _runs_on_text(job):
    r = job.get("runs-on", "")
    return str(r).lower()


def _matrix_text(job):
    return str(job.get("strategy", {}).get("matrix", "")).lower()


# Explicit exception: macOS-only iOS build of mobile/**; not a Windows runner, tracked separately.
EXCEPTIONS = {"mobile-ios.yml": "macOS iOS build, outside #1547 (Windows/install/e2e)"}


def _pr_event(doc):
    """Return (present, branches) for the pull_request event in mapping, list or scalar `on:` form."""
    on = _on(doc)
    if isinstance(on, str):
        return on == "pull_request", None
    if isinstance(on, list):
        return "pull_request" in on, None
    if isinstance(on, dict) and "pull_request" in on:
        return True, (on["pull_request"] or {}).get("branches")
    return False, None


@pytest.mark.parametrize("path", sorted(WF.glob("*.yml")), ids=lambda p: p.name)
def test_pr_to_dev_starts_no_windows_or_macos_job(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    present, branches = _pr_event(doc)
    if not present:
        return  # workflow has no pull_request trigger
    if branches is not None and not any(fnmatch.fnmatchcase("dev", b) for b in branches):
        return  # PRs into main only (release PR), not dev PRs
    if path.name in EXCEPTIONS:
        return
    for name, job in doc.get("jobs", {}).items():
        text = _runs_on_text(job) + _matrix_text(job)
        if not any(k in text for k in EXPENSIVE):
            continue
        cond = str(job.get("if", ""))
        assert "github.event_name != 'pull_request'" in cond, f"{path.name}:{name} runs on {text} for PRs into dev"


def test_pr_event_forms():
    assert _pr_event({"on": "pull_request"}) == (True, None)
    assert _pr_event({"on": ["push", "pull_request"]}) == (True, None)
    assert _pr_event({"on": {"push": None}}) == (False, None)
    assert _pr_event({"on": {"pull_request": {"branches": ["main"]}}}) == (True, ["main"])
