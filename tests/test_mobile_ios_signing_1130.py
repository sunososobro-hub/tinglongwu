"""#1130: the iOS archive step must not sign (no certificate created per CI run)."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "mobile-ios.yml"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash (the iOS workflow steps run in bash)")


def _step_script(name):
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == name:
                return step["run"]
    raise AssertionError(name)


def _run(tmp_path, step):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    stub = bindir / "xcodebuild"
    stub.write_text('#!/bin/bash\necho "$@" >> "$CALLS"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", CALLS=str(log),
               RUNNER_TEMP=str(tmp_path), ASC_KEY_ID="K", ASC_ISSUER_ID="I", APPLE_TEAM_ID="T")
    script = _step_script(step)
    r = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return log.read_text().splitlines()


def test_archive_is_unsigned_and_export_signs(tmp_path):
    calls = _run(tmp_path, "Archive")
    assert len(calls) == 1 and calls[0].startswith("archive ")
    assert "CODE_SIGNING_ALLOWED=NO" in calls[0]
    assert "-allowProvisioningUpdates" not in calls[0]
    assert "CODE_SIGN_STYLE=Automatic" not in calls[0]


def test_export_step_signs_with_api_key(tmp_path):
    _run(tmp_path, "Archive")
    (tmp_path / "calls.log").unlink()
    calls = _run(tmp_path, "Export and upload to App Store Connect")
    assert len(calls) == 1 and calls[0].startswith("-exportArchive")
    assert "-authenticationKeyPath" in calls[0] and "-allowProvisioningUpdates" in calls[0]
