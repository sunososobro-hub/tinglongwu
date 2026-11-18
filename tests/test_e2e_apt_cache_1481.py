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
    # the key rotates weekly (a newer package version gets a new entry); the prefix restore-key still finds the last one
    assert "steps.changes.outputs.aptweek" in r["key"] and r["key"].startswith(r["restore-keys"])
    assert "aptweek=" in steps[_index(steps, lambda x: x.get("id") == "changes")]["run"]


def _run_step(tmp_path, apt_body: str):
    steps, inst, restore, _ = _cache_steps()
    assert restore, "no actions/cache/restore step before the apt install"
    home = tmp_path / "home"
    home.mkdir()
    cached = Path(str(steps[restore[-1]]["with"]["path"]).replace("~", str(home), 1))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "apt.log"
    mirrors = tmp_path / "apt-mirrors.txt"
    mirrors.write_text("http://slow.example/ubuntu/\n")
    # like real apt, the fake leaves a root-only lock file in the archives folder
    lock = f'case " $* " in *" install "*) touch "{cached}/lock";; esac\n'
    for name, body in {"sudo": 'exec "$@"\n', "apt-get": f'echo "$@" >> "{log}"\n' + lock + apt_body,
                       "sleep": "exit 0\n"}.items():
        f = bin_ / name
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    env = {"HOME": str(home), "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "TLWU_APT_MIRRORS": str(mirrors)}
    out = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", steps[inst]["run"]], env=env,
                         capture_output=True, text=True, timeout=30)
    installs = [line for line in log.read_text().splitlines() if " install " in f" {line} "] if log.exists() else []
    return out, installs, cached, mirrors


@pytest.mark.skipif(BASH is None or shutil.which("timeout") is None, reason="bash and timeout are needed to run the step")
def test_install_step_points_apt_at_the_cached_folder(tmp_path):
    out, installs, cached, mirrors = _run_step(tmp_path, "exit 0\n")
    assert out.returncode == 0, out.stderr
    # download into the cached folder (time-capped), then install from it (never killed mid-dpkg)
    assert len(installs) == 2 and "--download-only" in installs[0] and "--download-only" not in installs[1]
    assert all(f"Dir::Cache::Archives={cached}" in line for line in installs)
    # apt leaves partial/ (_apt 0700) and lock (root 0640); the cache save runs without sudo, so the step removes both
    assert cached.is_dir() and not (cached / "partial").exists() and not (cached / "lock").exists()
    assert "slow.example" in mirrors.read_text()  # a working mirror is left alone


@pytest.mark.skipif(BASH is None or shutil.which("timeout") is None, reason="bash and timeout are needed to run the step")
def test_a_failed_try_switches_to_another_mirror_and_retries(tmp_path):
    flag = tmp_path / "failed_once"
    body = f'case " $* " in *" --download-only "*) [ -e "{flag}" ] && exit 0; : > "{flag}"; exit 100;; esac\nexit 0\n'
    out, installs, cached, mirrors = _run_step(tmp_path, body)
    assert out.returncode == 0, out.stderr
    downloads = [line for line in installs if "--download-only" in line]
    assert len(downloads) == 2 and len(installs) == 3  # failed download, retried download, one real install
    text = mirrors.read_text()
    assert "slow.example" not in text and "archive.ubuntu.com" in text
