"""#26: release.yml publishes GitHub build provenance for every release asset, with OIDC rights only where needed."""
from __future__ import annotations

import fnmatch
import re
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


def test_attest_action_is_pinned_to_a_commit_sha():
    steps = [s for j in _wf()["jobs"].values() for s in _attest_steps(j)]
    assert len(steps) == 2
    for s in steps:
        assert re.fullmatch(re.escape(ATTEST) + r"[0-9a-f]{40}", s["uses"]), s["uses"]
        assert s.get("continue-on-error") is True   # provenance never blocks a publish


def test_every_uploaded_release_file_is_attested():
    job = _wf()["jobs"]["release"]
    (step,) = _attest_steps(job)
    subjects = _subjects(step)
    create = _step(job, "Create the draft GitHub Release")
    files = re.search(r"files=\((.*?)\)", create["run"], re.S).group(1).split()
    uploaded = [f for f in files if f.startswith("dist/") and not f.endswith("SHA256SUMS.txt")]
    assert len(uploaded) >= 6, files
    for f in uploaded:
        assert any(fnmatch.fnmatch(f, pat) for pat in subjects), f"{f} not attested"
    # the optional Vulkan pack (added through the vulkan array, not the literal list) is attested when built
    assert any(fnmatch.fnmatch("dist/tlwu-vulkan-pack-0.3.6.zip", pat) for pat in subjects)
    assert job["steps"].index(step) < job["steps"].index(create)   # attested before the draft goes up


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
