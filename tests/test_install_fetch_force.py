"""#370: installer update path fetches tags with --force and reports git's real error."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"),
                                reason="needs bash and git")


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"})


def test_rewritten_tag_does_not_block_fetch(tmp_path):
    """Real git: a local tag moved away from the remote one; the installer's fetch must still succeed."""
    up = tmp_path / "up"; up.mkdir()
    _git("init", "-q", "-b", "main", cwd=up)
    (up / "a").write_text("1"); _git("add", "a", cwd=up); _git("commit", "-qm", "1", cwd=up)
    _git("tag", "v1", cwd=up)
    clone = tmp_path / "c"
    _git("clone", "-q", str(up), str(clone), cwd=tmp_path)
    (up / "a").write_text("2"); _git("commit", "-qam", "2", cwd=up)
    _git("tag", "-f", "v1", cwd=up)  # upstream rewrote the tag
    # extract the fetch command exactly as install.sh runs it
    line = next(l for l in (ROOT / "scripts" / "install.sh").read_text().splitlines()
                if 'fetch --quiet' in l and '--tags' in l)
    cmd = line.strip().removeprefix("if ! FETCH_ERR=$(").split(")")[0].replace('"$DIR"', str(clone))
    plain = subprocess.run(["bash", "-c", cmd.replace("--force ", "")], capture_output=True, text=True)
    assert plain.returncode != 0  # without --force this is the bug
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    tag = subprocess.run(["git", "-C", str(clone), "rev-parse", "v1"], capture_output=True, text=True).stdout
    assert tag == subprocess.run(["git", "-C", str(up), "rev-parse", "v1"], capture_output=True, text=True).stdout


def test_fetch_error_shows_git_stderr(tmp_path):
    fake = tmp_path / "bin"; fake.mkdir()
    for n, body in {"python3": "exit 1", "id": "echo 1000", "dpkg": "exit 0",
                    "git": 'echo "fatal: tag clash" >&2; exit 1'}.items():
        (fake / n).write_text("#!/bin/sh\n" + body + "\n"); (fake / n).chmod(0o755)
    (fake / "python3.12").write_text('#!/bin/sh\necho "Python 3.12.0"\n'); (fake / "python3.12").chmod(0o755)
    d = tmp_path / "tlwu"; (d / ".git").mkdir(parents=True)
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install.sh")], stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=60, env={"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path),
                                                   "TLWU_DIR": str(d), "TLWU_UI_LANG": "en"})
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "fatal: tag clash" in out
    assert "network?" not in out
