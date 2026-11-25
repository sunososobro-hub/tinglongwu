"""#1898: eval-fragments.yml runs the fragment set with the built-in term tables off and on and puts both tables in the
step summary. The step's own shell script is run with `python` pointed at the real `tlwu_eval` CLI and a stub MT."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "eval-fragments.yml"

# Runs `python -m tlwu_eval ...` as the real CLI, with an echo translator instead of the Hy-MT2 model.
SHIM = """import json, sys
sys.path.insert(0, {eval_dir!r})
from tlwu_eval import __main__ as ev
argv = sys.argv[1:]
assert argv[:2] == ["-m", "tlwu_eval"], argv
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps(argv[2:]) + "\\n")
sys.exit(ev.main(argv[2:], translate=lambda text, src, tgt, context: text))
"""


def _step_script():
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = wf["jobs"]["fragments"]["steps"]
    runs = [s["run"] for s in steps if "run" in s and "--fragment-cases" in s["run"]]
    assert len(runs) == 1
    return runs[0]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_step_writes_tech_off_and_on_tables_to_the_summary(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "argv.jsonl"
    shim = tmp_path / "shim.py"
    shim.write_text(SHIM.format(eval_dir=str(ROOT / "eval"), log=str(log)), encoding="utf-8")
    py = bindir / "python"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{shim}" "$@"\n', encoding="utf-8")
    py.chmod(0o755)
    work = tmp_path / "work"
    (work / "eval").mkdir(parents=True)
    shutil.copy(ROOT / "eval" / "fragment_cases.jsonl", work / "eval" / "fragment_cases.jsonl")
    summary = tmp_path / "summary.md"
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}", GITHUB_STEP_SUMMARY=str(summary),
               TLWU_MODELS=str(tmp_path / "models"), PYTHONUTF8="1")
    r = subprocess.run(["bash", "-e", "-c", _step_script()], cwd=work, env=env, capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0, r.stderr

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    scen = [c[c.index("--term-scenario") + 1] for c in calls]
    assert scen == ["none", "default"]

    text = summary.read_text(encoding="utf-8")
    off, on = text.index("### tech off"), text.index("### tech on")
    assert off < on
    for part in (text[off:on], text[on:]):
        assert "kind | condition | n | chrF | invented %" in part
        assert "\npolysemy | " in part and "\nidiom | " in part
    for name in ("none", "default"):
        rep = json.loads((work / "out" / f"fragments_{name}.json").read_text(encoding="utf-8"))
        assert {"polysemy", "idiom"} <= set(rep["kinds"])
