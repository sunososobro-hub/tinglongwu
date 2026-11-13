"""#516: the documented benchmark repro commands must still be accepted by scripts/bench/run.py (the removed LLM
engines are not runnable any more), and the user-facing CLI help lists no removed engine."""
from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["docs/benchmarks.md", "docs/readme-full.zh-TW.md", "docs/readme-full.zh-CN.md"]


class Reached(Exception):
    """Raised by the stubs once run.main() has validated its arguments (no data, model or network needed)."""


def _run_module():
    sys.path.insert(0, str(ROOT / "scripts" / "bench"))
    try:
        spec = importlib.util.spec_from_file_location("bench_run_516", ROOT / "scripts" / "bench" / "run.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.remove(str(ROOT / "scripts" / "bench"))


def _documented_argvs(doc: str) -> list[list[str]]:
    out = []
    for line in (ROOT / doc).read_text("utf-8").splitlines():
        if "scripts/bench/run.py" in line and line.lstrip().startswith(".venv-bench"):
            argv = shlex.split(line.replace("\\", "/"))
            out.append(argv[argv.index("scripts/bench/run.py") + 1:])
    return out


@pytest.mark.parametrize("doc", DOCS)
def test_documented_bench_commands_pass_engine_validation(doc, tmp_path, monkeypatch):
    run = _run_module()
    argvs = _documented_argvs(doc)
    assert argvs, doc

    def stop(*a, **k):
        raise Reached

    monkeypatch.setattr(run, "other_pair", stop)
    monkeypatch.setattr(run, "fleurs_rows", stop)
    for argv in argvs:
        with pytest.raises(Reached):  # an unknown engine exits earlier with SystemExit("unknown engine ...")
            run.main([*argv, "--no-google", "--cache", str(tmp_path / "cache")])


def test_tlwu_help_lists_no_removed_engine(capsys):
    from tinglongwu import cli

    with pytest.raises(SystemExit):
        cli.parse_args(["--help"])
    out = capsys.readouterr().out.lower()
    assert "--engine" in out and "hymt" in out
    assert "qwen" not in out and "m2m" not in out
