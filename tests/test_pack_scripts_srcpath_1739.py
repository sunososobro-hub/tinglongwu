"""#1739: the release's Vulkan pack jobs run these scripts with no install; since the src/ layer layout (#1664) they
must put the src/ layers on sys.path themselves, or `import tinglongwu` fails and the release ships no Vulkan pack."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = ("build_whisper_vulkan.py", "make_vulkan_pack.py")

# -S: no site-packages, so an installed tinglongwu cannot hide a missing path entry; only the script's own setup counts.
PROBE = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('probe', sys.argv[1])\n"
    "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
    "print(importlib.util.find_spec('tinglongwu').origin)\n"
)


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_finds_tinglongwu_without_install(name, tmp_path):
    out = subprocess.run([sys.executable, "-S", "-c", PROBE, str(REPO / "scripts" / name)], cwd=tmp_path,
                         capture_output=True, text=True, env={"PATH": "", "SYSTEMROOT": ""}, timeout=60)
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()).resolve().is_relative_to(REPO / "src" / "core" / "tinglongwu")
