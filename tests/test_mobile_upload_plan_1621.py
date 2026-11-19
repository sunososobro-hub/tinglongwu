import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mup", ROOT / "scripts" / "mobile_upload_plan.py")
mup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mup)
R = "o/r"


def test_dev_push_uploads():
    assert mup.should_upload("push", "refs/heads/dev", R, "", True)


def test_other_push_does_not_upload():
    assert not mup.should_upload("push", "refs/heads/fix/1", R, "", True)


def test_same_repo_pr_uploads():
    assert mup.should_upload("pull_request", "refs/pull/5/merge", R, R, True)


def test_fork_pr_never_uploads():
    assert not mup.should_upload("pull_request", "refs/pull/5/merge", R, "evil/r", True)
    assert not mup.should_upload("pull_request", "refs/pull/5/merge", R, "", True)


def test_no_play_secrets_no_upload():
    assert not mup.should_upload("push", "refs/heads/dev", R, "", False)
    assert not mup.should_upload("pull_request", "x", R, R, False)


def test_manual_dispatch_does_not_upload():
    assert not mup.should_upload("workflow_dispatch", "refs/heads/dev", R, "", True)


def test_cli_prints_upload():
    out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "mobile_upload_plan.py"),
                          "pull_request", "refs/pull/1/merge", R, R, "true"],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "upload=true"
