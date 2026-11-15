"""#172: install-test's linux job downloads a small model and runs the CPU self-test with a 60 s cap."""
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "install-test.yml"


def _steps():
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return wf["jobs"]["linux"]["steps"]


def test_linux_job_runs_cpu_selftest_after_real_download():
    steps = _steps()
    idx = [i for i, s in enumerate(steps) if "--selftest" in s.get("run", "")]
    assert len(idx) == 1, "exactly one selftest step expected"
    run = steps[idx[0]]["run"]
    assert "--device cpu" in run and "-lt 60" in run
    assert "--whisper tiny" in run and "--hymt" in run
    assert "requirements-llama-cpu.txt" in run and run.index("requirements-llama-cpu.txt") < run.index("--selftest")
    assert run.index("--whisper tiny") < run.index("--selftest")
    names = [s.get("name", "") for s in steps]
    assert idx[0] < next(i for i, n in enumerate(names) if n.startswith("Uninstall"))
