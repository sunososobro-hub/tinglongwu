"""#1225: release-pr.yml's "Is this still the dev tip?" step must not starve the snapshot on a busy dev.

Runs the step's real shell script in a throwaway repo: a ci run for an older commit with the same
__version__ as the dev tip goes on; one with an older __version__ is still stale.
"""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-pr.yml"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")


def _tip_script() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text("utf-8"))
    for step in wf["jobs"]["plan"]["steps"]:
        if step.get("id") == "tip":
            return step["run"]
    raise AssertionError("step id 'tip' not found")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    _git(tmp_path, "init", "-q", "-b", "dev", str(work))
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@t")
    shas = []
    for i, v in enumerate(["0.4.1", "0.4.2", "0.4.2"]):
        (work / "src" / "core" / "tinglongwu").mkdir(parents=True, exist_ok=True)
        (work / "src" / "core" / "tinglongwu" / "__init__.py").write_text(f'__version__ = "{v}"\n')
        (work / "f.txt").write_text(str(i))
        _git(work, "add", "-A")
        _git(work, "commit", "-qm", v)
        shas.append(_git(work, "rev-parse", "HEAD"))
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "dev", f"{shas[0]}:refs/heads/main")
    return work, shas


def _run(work: Path, run_sha: str) -> str:
    out = work / "out.txt"
    summary = work / "summary.txt"
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "RUN_SHA": run_sha, "GITHUB_OUTPUT": str(out),
           "GITHUB_STEP_SUMMARY": str(summary), "HOME": str(work)}
    subprocess.run(["bash", "-e", "-c", _tip_script()], cwd=work, env=env, check=True, capture_output=True)
    return out.read_text().strip()


def test_older_commit_same_version_goes_on(repo):
    work, shas = repo
    assert _run(work, shas[1]) == "go=true"  # the bump commit; dev moved on with the same version


def test_older_version_is_still_stale(repo):
    work, shas = repo
    assert _run(work, shas[0]) == "go=false"


def test_tip_goes_on(repo):
    work, shas = repo
    assert _run(work, shas[2]) == "go=true"
