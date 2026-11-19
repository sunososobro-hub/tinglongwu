"""#1596: per-platform release lanes. After the shared gate (ci `test` + offline suite) Windows, macOS and Linux each
pass only their own jobs: a failed macOS / Linux job no longer blocks the release (tag, Windows exe), and that platform
stays on its last good version via its marker tag (mac-ok / linux-ok), which the Mac / Linux installers and the
menu's auto-update follow."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_release_notes import GREEN, HEAD, OPEN, REPO, TAGS, WN, plan, pr, rn

ROOT = Path(__file__).resolve().parent.parent

E2E_JOBS = ["linux", "windows", "macos"]
CLEAN_JOBS = ["install-bat", "installer-exe", "ttfs (linux, ubuntu-24.04)", "ttfs (macos, macos-latest)",
              "ttfs (windows, windows-latest)", "mt-handover", "speak-smoke"]
RUN_ID = {n: r["id"] for r in GREEN for n in [r["name"]]}


def runs_and_jobs(failed=()):
    """GREEN with real job lists for e2e / install-clean; jobs named in `failed` fail (and so does their run)."""
    jobs = {RUN_ID["e2e"]: E2E_JOBS, RUN_ID["install-clean"]: CLEAN_JOBS}
    runs = [dict(r, conclusion="failure") if any(j in failed for j in jobs.get(r["id"], [])) else r for r in GREEN]
    jobs_of = lambda rid: [{"name": n, "status": "completed", "conclusion": "failure" if n in failed else "success"}
                           for n in jobs.get(rid, ["job"])]
    return runs, jobs_of


def test_job_platform_by_name():
    assert [rn.job_platform(n) for n in E2E_JOBS] == ["linux", "windows", "mac"]
    assert [rn.job_platform(n) for n in CLEAN_JOBS] == ["windows", "windows", "linux", "mac", "windows", "windows",
                                                        "windows"]


def test_failed_macos_job_ships_windows_and_skips_mac():
    runs, jobs_of = runs_and_jobs(failed=("macos", "ttfs (macos, macos-latest)"))
    assert rn.check_problems(runs, jobs_of=jobs_of) == []
    out = plan([pr()], runs=runs, jobs_of=jobs_of)
    assert out["publish"] == "true" and out["tag"] == "v0.3.2"
    assert out["platforms"] == "mac=skip linux=ok"


def test_failed_linux_job_does_not_block_either():
    runs, jobs_of = runs_and_jobs(failed=("linux",))
    assert plan([pr()], runs=runs, jobs_of=jobs_of)["platforms"] == "mac=ok linux=skip"


def test_failed_windows_job_still_blocks_the_release():
    for job in ("windows", "speak-smoke", "ttfs (windows, windows-latest)"):
        runs, jobs_of = runs_and_jobs(failed=(job, "macos"))
        assert rn.check_problems(runs, jobs_of=jobs_of), job
        with pytest.raises(SystemExit):
            plan([pr()], runs=runs, jobs_of=jobs_of)


def test_without_job_lists_a_failed_run_still_blocks():
    runs, _ = runs_and_jobs(failed=("macos",))
    assert rn.check_problems(runs) != []   # pre-release tags (no jobs_of) keep the strict gate


def test_all_green_ships_every_platform():
    runs, jobs_of = runs_and_jobs()
    assert plan([pr()], runs=runs, jobs_of=jobs_of)["platforms"] == "mac=ok linux=ok"


def test_skipped_or_missing_platform_jobs_are_not_verified():
    runs, _ = runs_and_jobs()
    skipped = lambda rid: [{"name": "macos", "status": "completed", "conclusion": "skipped"},
                           {"name": "linux", "status": "completed", "conclusion": "success"}]
    assert rn.platform_status(runs, skipped) == {"mac": False, "linux": True}
    assert rn.platform_status(runs, lambda rid: []) == {"mac": False, "linux": False}


def test_auto_release_merges_when_only_mac_failed():
    runs, jobs_of = runs_and_jobs(failed=("macos",))
    out = rn.auto_plan("true", "level: 2", HEAD, "0.3.2", TAGS, [OPEN], REPO, lambda h: runs, WN, [],
                       jobs_of)
    assert out["merge"] == "true", out["reason"]
    runs, jobs_of = runs_and_jobs(failed=("windows",))
    out = rn.auto_plan("true", "level: 2", HEAD, "0.3.2", TAGS, [OPEN], REPO, lambda h: runs, WN, [],
                       jobs_of)
    assert out["merge"] == "false" and "e2e" in out["reason"]


def test_platform_plan_and_notes():
    tags = {"v0.3.0": "3" * 40, "v0.3.1": "4" * 40}
    # mac failed and its marker sits on v0.3.0; linux passed
    p = rn.platform_plan("v0.3.2", {"mac": False, "linux": True}, {"mac-ok": "3" * 40, "linux-ok": "4" * 40}, tags)
    assert p["mac"] == {"ship": False, "version": "v0.3.0", "move_to": ""}
    assert p["linux"] == {"ship": True, "version": "v0.3.2", "move_to": "v0.3.2"}
    md = rn.platforms_md(p)
    assert "- Windows: shipped v0.3.2" in md
    assert "- macOS: skipped (not verified), stays on v0.3.0" in md
    assert "- Linux: shipped v0.3.2" in md
    # first release with lanes: no marker yet -> created at the previous release, never at the unverified one
    p = rn.platform_plan("v0.3.2", {"mac": False, "linux": False}, {"mac-ok": None, "linux-ok": None}, tags)
    assert p["mac"] == {"ship": False, "version": "v0.3.1", "move_to": "v0.3.1"}
    notes = rn.release_notes("## What's new\n- TODO: x\n", "", None, "ok", "", HEAD, [], {}, platforms=p)
    assert "### Platforms" in notes and "- Linux: skipped (not verified), stays on v0.3.1" in notes
    assert rn.parse_platforms("mac=ok linux=skip") == {"mac": True, "linux": False}


# ---------------------------------------------------------------- installers / auto-update follow the marker

needs_bash = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"),
                                reason="needs bash + git")


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """v1 (last good on mac / linux) -> v2 (released, mac failed) -> main tip (unreleased)."""
    up = tmp_path / "up"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    for name in ("v1", "v2", "tip"):
        (up / "f.txt").write_text(name)
        git(up, "add", ".")
        git(up, "commit", "-qm", name)
        if name != "tip":
            git(up, "tag", name)
    return up


@needs_bash
@pytest.mark.parametrize("script,marker,var", [("mac_install.sh", "mac-ok", "TLWU_BRANCH"),
                                               ("install.sh", "linux-ok", "TLWU_REF")])
def test_installer_dry_run_resolves_the_marker(upstream, tmp_path, script, marker, var):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TLWU_REPO": str(upstream)}
    run = lambda: subprocess.run(["bash", str(ROOT / "scripts" / script), "--dry-run"], env=env, capture_output=True,
                                 text=True, timeout=60).stdout
    assert run().strip() == "ref: main"   # no marker on the server yet
    git(upstream, "tag", marker, "v1")
    assert run().strip() == f"ref: {marker}"
    released = (ROOT / "scripts" / script).read_text("utf-8").replace("__TLWU_REF__", marker)  # = release.yml's sed
    rel = tmp_path / script
    rel.write_text(released, "utf-8")
    out = subprocess.run(["bash", str(rel), "--dry-run"], env=env, capture_output=True, text=True, timeout=60).stdout
    assert out.strip() == f"ref: {marker}"
    assert subprocess.run(["bash", str(rel), "--dry-run"], env={**env, var: "v2"}, capture_output=True,
                          text=True).stdout.strip() == "ref: v2"


@needs_bash
@pytest.mark.parametrize("os_name,marker", [("Darwin", "mac-ok"), ("Linux", "linux-ok")])
def test_auto_update_follows_the_marker_not_main(upstream, tmp_path, os_name, marker):
    work, stubs = tmp_path / "work", tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "uname").write_text(f"#!/bin/sh\necho {os_name}\n")
    (stubs / "uname").chmod(0o755)
    git(tmp_path, "clone", "-q", "--branch", "v1", str(upstream), str(work))   # what the installer leaves: detached
    git(upstream, "tag", marker, "v1")
    script = f'm(){{ :; }}; mp(){{ :; }}; . {ROOT}/scripts/auto_update.sh; tlwu_auto_update; echo "R=$TLWU_UPDATE_RESULT"'
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path)}
    run = lambda: subprocess.run(["bash", "-c", script], cwd=work, env=env, capture_output=True, text=True).stdout
    assert "R=uptodate" in run()   # v2 failed this platform: stay on v1, never main tip
    assert (work / "f.txt").read_text() == "v1"
    git(upstream, "tag", "-f", marker, "v2")   # the next release passed: the marker moved
    assert "R=updated" in run()
    assert (work / "f.txt").read_text() == "v2"


def test_release_workflow_notes_step_lists_platforms(tmp_path, monkeypatch):
    """The `notes` command exactly as release.yml runs it, with plan's platforms output: mac skipped, stays on v0.3.0."""
    from test_release_notes import _release_yml_notes_argv
    (tmp_path / "base.md").write_text("## What's new\n- TODO: x\n", "utf-8")
    (tmp_path / "release-check.md").write_text("size OK\n", "utf-8")
    argv = _release_yml_notes_argv({"TAG": "v0.3.2", "PR": "", "HW_RUN": "", "HEAD": "", "DIST": str(tmp_path),
                                    "PLATFORMS": "mac=skip linux=ok"})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packaging").symlink_to(ROOT / "packaging")
    monkeypatch.setattr(rn, "repo", lambda: "o/r")
    monkeypatch.setattr(rn, "history", lambda rep, tag, tip="HEAD": (None, [], {}))
    monkeypatch.setattr(rn, "git_tags", lambda: {"v0.3.0": "3" * 40, "v0.3.1": "4" * 40})
    monkeypatch.setattr(rn, "marker_shas", lambda: {"mac-ok": "3" * 40, "linux-ok": "4" * 40})
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert rn.main(argv) == 0
    notes = (tmp_path / "notes.md").read_text("utf-8")
    assert "- Windows: shipped v0.3.2" in notes and "- Linux: shipped v0.3.2" in notes
    assert "- macOS: skipped (not verified), stays on v0.3.0" in notes


def test_markers_command_moves_only_verified_platforms(monkeypatch, capsys):
    monkeypatch.setattr(rn, "git_tags", lambda: {"v0.3.0": "3" * 40, "v0.3.1": "4" * 40})
    monkeypatch.setattr(rn, "marker_shas", lambda: {"mac-ok": "3" * 40, "linux-ok": None})
    assert rn.main(["markers", "--version", "v0.3.2", "--platforms", "mac=skip linux=ok"]) == 0
    assert capsys.readouterr().out.split("\n") == ["linux-ok v0.3.2", ""]
