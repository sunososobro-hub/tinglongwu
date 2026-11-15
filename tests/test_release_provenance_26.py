"""#26: release.yml publishes GitHub build provenance for every release asset, with OIDC rights only where needed."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
ATTEST = "actions/attest-build-provenance@"
ATTEST_JOBS = {"release", "windows-installer"}


def _wf():
    return yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))


def _attest_steps(job):
    return [s for s in job.get("steps", []) if str(s.get("uses", "")).startswith(ATTEST)]


def _subjects(step):
    return [p.strip() for p in str(step["with"]["subject-path"]).splitlines() if p.strip()]


def _step(job, prefix):
    return next(s for s in job["steps"] if str(s.get("name", "")).startswith(prefix))


def test_attest_action_is_github_owned_and_never_blocks_a_publish():
    steps = [s for j in _wf()["jobs"].values() for s in _attest_steps(j)]
    assert len(steps) == 2
    for s in steps:
        # a 40-hex SHA or a major tag of the GitHub-owned action (an unverified SHA would fail "Set up job")
        assert re.fullmatch(re.escape(ATTEST) + r"([0-9a-f]{40}|v\d+)", s["uses"]), s["uses"]
        assert s.get("continue-on-error") is True   # provenance never blocks a publish


def _listed(job, tmp_path, files):
    """Run the release job's attest-list step in a fake workspace holding `files`; return the listed paths."""
    step = next(s for s in job["steps"] if s.get("id") == "attest-list")
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"x")
    out = tmp_path / "gh_output"
    out.write_text("")
    subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, check=True,
                   env={**os.environ, "GITHUB_OUTPUT": str(out)})
    text = out.read_text()
    m = re.fullmatch(r"paths<<(\w+)\n(.*?)\1\n", text, re.S)
    assert m, text
    return m.group(2).split()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_every_uploaded_release_file_is_attested(tmp_path):
    job = _wf()["jobs"]["release"]
    (step,) = _attest_steps(job)
    assert step["with"]["subject-path"] == "${{ steps.attest-list.outputs.paths }}"
    create = _step(job, "Create the draft GitHub Release")
    files = re.search(r"files=\((.*?)\)", create["run"], re.S).group(1).split()
    uploaded = [f for f in files if f.startswith("dist/") and not f.endswith("SHA256SUMS.txt")]
    assert len(uploaded) >= 6, files
    listed = _listed(job, tmp_path, uploaded + ["dist/tlwu-vulkan-pack-0.3.6.zip", "dist/other.txt"])
    assert listed == uploaded + ["dist/tlwu-vulkan-pack-0.3.6.zip"]  # the optional Vulkan pack when built
    steps = job["steps"]
    lst = next(s for s in steps if s.get("id") == "attest-list")
    assert steps.index(lst) < steps.index(step) < steps.index(create)   # attested before the draft goes up


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_no_vulkan_pack_still_attests_the_other_assets(tmp_path):
    job = _wf()["jobs"]["release"]
    core = ["dist/tinglongwu-win64.zip", "dist/tinglongwu-gpu-pack.zip", "dist/tinglongwu-gpu-pack.zip.manifest.json",
            "dist/install.sh", "dist/mac_install.sh", "dist/install.ps1"]
    listed = _listed(job, tmp_path, core)
    assert listed == core   # no unmatched glob left in the list (it would fail the attest step for every file)
    assert not any("*" in p for p in listed)


def test_wizard_and_final_checksums_are_attested_on_publish_only_before_upload():
    job = _wf()["jobs"]["windows-installer"]
    (step,) = _attest_steps(job)
    assert step["if"] == "needs.plan.outputs.publish == 'true'"
    subjects = _subjects(step)
    assert any(fnmatch.fnmatch("dist/installer/tinglongwu-setup-0.3.6.exe", p) for p in subjects)
    assert "dist/installer/SHA256SUMS.txt" in subjects  # the published file, after the wizard lines are added
    steps = job["steps"]
    sums = next(s for s in steps if "SHA256SUMS.txt" in s.get("run", "") and "sha256sum" in s.get("run", ""))
    up = _step(job, "Upload tinglongwu-setup-")
    assert steps.index(sums) < steps.index(step) < steps.index(up)  # attest the final bytes, then upload them
    assert "sha256sum" not in up["run"]  # nothing rewrites SHA256SUMS.txt after it was attested


def test_oidc_and_attestation_write_only_on_attesting_jobs():
    wf = _wf()
    assert wf["permissions"] == {}
    for name, job in wf["jobs"].items():
        perms = job.get("permissions", {})
        if name in ATTEST_JOBS:
            assert perms.get("id-token") == "write" and perms.get("attestations") == "write", name
        else:
            assert "id-token" not in perms and "attestations" not in perms, name
            assert not _attest_steps(job), name
