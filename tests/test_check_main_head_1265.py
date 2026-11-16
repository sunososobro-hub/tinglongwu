"""#1265: PRs into main are only allowed from release/vX.Y.Z or docs/fastlane-N."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_main_head.py"


def run(base, head):
    return subprocess.run([sys.executable, str(SCRIPT), base, head], capture_output=True, text=True)


@pytest.mark.parametrize("head", ["feat/conversation-mode", "docs/code-signing-policy", "dev", "release/v1.2", "fix/12"])
def test_other_heads_into_main_fail_and_name_the_rule(head):
    r = run("main", head)
    assert r.returncode == 1
    assert "release/vX.Y.Z" in r.stdout and head in r.stdout


@pytest.mark.parametrize("head", ["release/v0.4.3", "docs/fastlane-1265"])
def test_release_and_fastlane_heads_pass(head):
    assert run("main", head).returncode == 0


def test_prs_into_dev_are_not_checked():
    assert run("dev", "feat/anything").returncode == 0
