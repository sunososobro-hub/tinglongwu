"""#888: dev pushes must finish and be the only runs that save the build cache (since #923: the runtime layer)."""
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "windows-build.yml"


def _wf():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _steps():
    return _wf()["jobs"]["build"]["steps"]


def test_only_pull_requests_cancel_in_progress():
    assert _wf()["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_cache_is_restore_then_save_from_push_only_after_build():
    steps = _steps()
    uses = [s.get("uses", "").split("@")[0] for s in steps]
    assert "actions/cache" not in uses  # the combined action saved from PRs too
    r, b, sv = uses.index("actions/cache/restore"), next(i for i, s in enumerate(steps) if s.get("id") == "build"), \
        uses.index("actions/cache/save")
    assert r < b < sv
    cond = steps[sv]["if"]
    assert "github.event_name == 'push'" in cond and "steps.build.outcome == 'success'" in cond and "always()" in cond
    # #923: the runtime layer has one entry per content key (no run_id part): saved under the key that missed
    assert steps[r]["id"] == "runtime" and steps[sv]["with"]["key"] == "${{ steps.runtime.outputs.cache-primary-key }}"
    assert "run_id" not in steps[r]["with"]["key"] and "github.sha" not in steps[r]["with"]["key"]
    assert "steps.runtime.outputs.cache-hit != 'true'" in cond
