"""#1766: v0.5.4 shipped tlwu-vulkan-pack-<v>.zip without the Linux pack and said nothing. The vulkan-pack-linux
job stalled 20 min in `apt-get update` (release run 45161027121) and was cancelled at the core job budget.
(1) release_check.py --vulkan-packs fails a release whose dist has no Linux pack unless the notes say why, and
--linux-skip-reason writes that line; (2) scripts/apt_retry.sh kills a stalled apt-get and retries."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts" / "release_check.py")
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

TAG = "v0.5.5"
WIN = "tlwu-vulkan-pack-0.5.5.zip"
LINUX = "tlwu-vulkan-pack-linux-0.5.5.zip"


def dist_with(tmp_path: Path, *names: str) -> tuple[Path, Path]:
    dist = tmp_path / "dist"
    dist.mkdir()
    for n in names:
        (dist / n).write_bytes(b"zip")
    notes = dist / "notes.md"
    notes.write_text("## v0.5.5\n\n- fixes\n", encoding="utf-8")
    return dist, notes


def run(dist: Path, notes: Path, *extra: str) -> int:
    return rc.main(["--vulkan-packs", str(dist), "--notes", str(notes), "--tag", TAG, *extra])


def test_windows_pack_without_linux_pack_and_no_notes_line_fails(tmp_path, capsys):
    dist, notes = dist_with(tmp_path, WIN)
    assert run(dist, notes) == 1
    out = capsys.readouterr().out
    assert f"FAIL: no {LINUX} (the Windows pack is attached)" in out


def test_no_pack_at_all_also_needs_the_line(tmp_path):
    dist, notes = dist_with(tmp_path)  # v0.5.3: neither pack, nothing said
    assert run(dist, notes) == 1


def test_skip_reason_appends_one_line_and_passes(tmp_path, capsys):
    dist, notes = dist_with(tmp_path, WIN)
    assert run(dist, notes, "--linux-skip-reason", "the vulkan-pack-linux job ended 'cancelled'") == 0
    text = notes.read_text(encoding="utf-8")
    assert text.startswith("## v0.5.5\n\n- fixes\n")
    lines = [ln for ln in text.splitlines() if rc.LINUX_VULKAN_SKIPPED in ln]
    assert lines == ["- Linux Vulkan pack skipped: the vulkan-pack-linux job ended 'cancelled'. Linux keeps the CPU "
                     "path; `tlwu-setup --vulkan-pack` finds no pack for 0.5.5 (the next release retries)."]
    assert "Linux Vulkan pack: skipped (the notes say why)" in capsys.readouterr().out
    # a re-run of the release job (draft retry) does not add a second line
    assert run(dist, notes, "--linux-skip-reason", "again") == 0
    assert notes.read_text(encoding="utf-8").count(rc.LINUX_VULKAN_SKIPPED) == 1


def test_both_packs_pass_and_leave_the_notes_alone(tmp_path, capsys):
    dist, notes = dist_with(tmp_path, WIN, LINUX)
    before = notes.read_text(encoding="utf-8")
    assert run(dist, notes, "--linux-skip-reason", "unused") == 0
    assert notes.read_text(encoding="utf-8") == before
    assert "Linux Vulkan pack: attached" in capsys.readouterr().out


def test_vulkan_packs_needs_notes_and_tag(tmp_path):
    with pytest.raises(SystemExit):
        rc.main(["--vulkan-packs", str(tmp_path)])


def test_release_job_runs_the_check_before_the_release_is_created():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    steps = [s.get("name", "") for s in wf["jobs"]["release"]["steps"]]
    check = next(i for i, n in enumerate(steps) if "#1766" in n)
    assert check < next(i for i, n in enumerate(steps) if n.startswith("Build install assets"))  # SHA256SUMS
    assert check < next(i for i, n in enumerate(steps) if n.startswith("Create the draft GitHub Release"))
    assert "--vulkan-packs dist --notes dist/notes.md" in wf["jobs"]["release"]["steps"][check]["run"]
    for job in ("vulkan-pack-linux", "whisper-vulkan"):
        runs = " ".join(s.get("run", "") for s in wf["jobs"][job]["steps"])
        assert "scripts/apt_retry.sh" in runs and "apt-get update" not in runs


# --- scripts/apt_retry.sh ----------------------------------------------------------------------------------------

def fake_apt(tmp_path: Path, stalls: int) -> tuple[dict, Path]:
    """PATH with a fake sudo (runs its args) and apt-get: `update` sleeps 60 s for the first `stalls` calls."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    count = tmp_path / "updates"
    (bin_ / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
    (bin_ / "apt-get").write_text(f"""#!/bin/sh
for a in "$@"; do last="$a"; case "$a" in update|install) verb="$a";; esac; done
echo "$verb $last" >> "{log}"
if [ "$verb" = update ]; then
  n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{count}"
  [ "$n" -gt {stalls} ] || exec sleep 60
fi
exit 0
""")
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", APT_UPDATE_TIMEOUT_S="1",
               APT_INSTALL_TIMEOUT_S="5", APT_RETRY_PAUSE_S="0")
    return env, log


needs_bash = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("timeout") is None or os.name == "nt",
                                reason="POSIX bash + coreutils timeout")


@needs_bash
def test_apt_retry_kills_a_stalled_update_and_retries(tmp_path):
    env, log = fake_apt(tmp_path, stalls=1)
    t0 = time.monotonic()
    r = subprocess.run(["bash", str(ROOT / "scripts" / "apt_retry.sh"), "glslc", "spirv-headers"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert time.monotonic() - t0 < 20  # the 60 s stall was cut at the 1 s timeout
    assert log.read_text().split("\n")[:3] == ["update -qq", "update -qq", "install spirv-headers"]
    assert "apt-get update attempt 1/2 failed (exit 124" in r.stdout


@needs_bash
def test_apt_retry_gives_up_after_the_tries(tmp_path):
    env, log = fake_apt(tmp_path, stalls=99)
    r = subprocess.run(["bash", str(ROOT / "scripts" / "apt_retry.sh"), "glslc"], env=env, capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 1
    assert "gave up after 2 attempts" in r.stdout
    assert "install" not in log.read_text()
