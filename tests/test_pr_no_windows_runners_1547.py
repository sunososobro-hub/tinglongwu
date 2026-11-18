"""#1547: a pull_request into dev must not start a job on a Windows / macOS runner."""
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


@pytest.mark.parametrize("path", sorted(WF.glob("*.yml")), ids=lambda p: p.name)
def test_pr_to_dev_starts_no_windows_or_macos_job(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    pr = _on(doc).get("pull_request") if isinstance(_on(doc), dict) else None
    if pr is None and "pull_request" not in (_on(doc) if isinstance(_on(doc), dict) else {}):
        return  # workflow has no pull_request trigger
    branches = (pr or {}).get("branches")
    if branches is not None and "dev" not in branches:
        return  # PRs into main only (release PR), not dev PRs
    if path.name in {"mobile-ios.yml"}:
        pytest.skip("mobile-ios: macOS runner, tracked separately")
    for name, job in doc.get("jobs", {}).items():
        text = _runs_on_text(job) + _matrix_text(job)
        if not any(k in text for k in EXPENSIVE):
            continue
        cond = str(job.get("if", ""))
        assert "github.event_name != 'pull_request'" in cond, f"{path.name}:{name} runs on {text} for PRs into dev"
