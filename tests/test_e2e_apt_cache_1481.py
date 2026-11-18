"""#1481: the e2e linux apt step installs from a cached .deb folder, so a slow Ubuntu mirror no longer times it out.

The install step's own bash runs with fake `sudo` / `apt-get` on PATH; the test checks where apt keeps its .debs
and that this is the folder the actions/cache restore + save steps carry between runs."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / ".github/workflows/e2e.yml"
BASH = shutil.which("bash")
STEP = "System packages (audio server, virtual display, window manager)"


def _steps():
    return yaml.safe_load(E2E.read_text(encoding="utf-8"))["jobs"]["linux"]["steps"]


def _index(steps, pred):
    return next(i for i, s in enumerate(steps) if pred(s))


def _cache_steps():
    steps = _steps()
    inst = _index(steps, lambda s: s.get("name") == STEP)
    restore = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/cache/restore@")]
    save = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/cache/save@")]
    restore = [i for i in restore if i < inst]
    save = [i for i in save if i > inst]
    return steps, inst, restore, save


def test_apt_cache_restored_before_and_saved_after_the_install():
    steps, inst, restore, save = _cache_steps()
    assert restore, "no actions/cache/restore step before the apt install"
    assert save, "no actions/cache/save step after the apt install"
    r, s = steps[restore[-1]]["with"], steps[save[0]]["with"]
    assert r["path"] == s["path"] and r["key"] == s["key"]
    assert steps[inst].get("timeout-minutes", 0) >= 15


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step")
def test_install_step_points_apt_at_the_cached_folder(tmp_path):
    steps, inst, restore, _ = _cache_steps()
    assert restore, "no actions/cache/restore step before the apt install"
    home = tmp_path / "home"
    home.mkdir()
    cached = Path(str(steps[restore[-1]]["with"]["path"]).replace("~", str(home), 1))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "apt.log"
    for name, body in {"sudo": 'exec "$@"\n', "apt-get": f'echo "$@" >> "{log}"\n', "sleep": "exit 0\n"}.items():
        f = bin_ / name
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    env = {"HOME": str(home), "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}"}
    out = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", steps[inst]["run"]], env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    install = [line for line in log.read_text().splitlines() if " install " in f" {line} "]
    assert install, log.read_text()
    assert f"Dir::Cache::Archives={cached}" in install[0]
    assert (cached / "partial").is_dir()
