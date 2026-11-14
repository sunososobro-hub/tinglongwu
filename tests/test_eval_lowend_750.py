"""#750: low-end CPU gate. Runs the real smoke runner + CLI with fake engines on a fake clock, so "a PR that adds
200 ms per line" is reproduced exactly: the gate must fail on it with the p50 numbers in its table, and pass again
without it. The workflow's own path-filter step is executed to check the release PR always runs the job."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tlwu_eval import lowend
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock, fake_engines, make_data

ROOT = Path(__file__).resolve().parent.parent


def _smoke(tmp_path, name, mt_s=0.2, rss=3000.0, extra_args=()):
    data = make_data(tmp_path / "data") if not (tmp_path / "data" / "manifest.jsonl").exists() else tmp_path / "data"
    out = tmp_path / f"{name}.json"
    clock = Clock()
    code = main(["--smoke", "--data", str(data), "--out", str(out), "--no-noise", *extra_args],
                engines=fake_engines(clock, mt_s=mt_s), totals=(16000, None), clock=clock, peak_rss=lambda: rss)
    assert code in (0, 1)
    return out


def _baseline(tmp_path, p50, p90):
    b = json.loads((ROOT / "eval/lowend_baseline.json").read_text("utf-8"))
    b.update(latency_p50_s=p50, latency_p90_s=p90)
    path = tmp_path / "base.json"
    path.write_text(json.dumps(b), "utf-8")
    return path


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "data").mkdir()
    make_data(tmp_path / "data")
    return tmp_path


def test_report_has_line_p50_p90_and_peak_rss(data_dir):
    rep = json.loads(_smoke(data_dir, "r", rss=4321.4).read_text("utf-8"))
    # every line: ASR 0.4 s + MT 0.2 s on the fake clock
    assert rep["latency_p50_s"] == 0.6 and rep["latency_p90_s"] == 0.6 and rep["peak_rss_mib"] == 4321


def test_200ms_per_line_fails_then_revert_passes(data_dir, capsys):
    base = _baseline(data_dir, 0.6, 0.6)  # measured on "dev"
    slow = _smoke(data_dir, "slow", mt_s=0.4)  # the PR adds a 200 ms sleep per line
    capsys.readouterr()
    assert main(["--lowend-gate", str(slow), "--lowend-baseline", str(base)]) == 1
    table = capsys.readouterr().out
    assert re.search(r"\| latency_p50_s \| 0\.8 \| 0\.6 \| 0\.69 \| FAIL \(33% worse", table)
    assert "**low-end CPU gate: FAIL**" in table
    ok = _smoke(data_dir, "ok", mt_s=0.2)  # reverted
    capsys.readouterr()
    assert main(["--lowend-gate", str(ok), "--lowend-baseline", str(base)]) == 0
    assert "**low-end CPU gate: PASS**" in capsys.readouterr().out


def test_within_15_percent_passes_and_just_over_fails(data_dir):
    base = _baseline(data_dir, 0.6, 0.6)
    assert lowend.run(_smoke(data_dir, "a", mt_s=0.29), base)[0] == 0  # 0.69 s = +15.0%
    assert lowend.run(_smoke(data_dir, "b", mt_s=0.30), base)[0] == 1  # 0.70 s = +16.7%


def test_p90_regression_alone_fails(data_dir):
    rep = json.loads(_smoke(data_dir, "r").read_text("utf-8"))
    rep["latency_p90_s"] = 2.0
    path = data_dir / "p90.json"
    path.write_text(json.dumps(rep), "utf-8")
    code, table = lowend.run(path, _baseline(data_dir, 0.6, 1.0))
    assert code == 1 and "| latency_p90_s | 2 | 1 | 1.15 | FAIL" in table and "| latency_p50_s | 0.6 |" in table


def test_peak_rss_over_6_gib_fails(data_dir):
    base = _baseline(data_dir, 0.6, 0.6)
    assert lowend.RSS_MAX_MIB == 6144
    assert lowend.run(_smoke(data_dir, "ok", rss=6144.0), base)[0] == 0
    code, table = lowend.run(_smoke(data_dir, "fat", rss=6200.0), base)
    assert code == 1 and "| peak_rss_mib | 6200 | n/a | 6144 | FAIL (over the 6144 MiB cap) |" in table


def test_null_baseline_reports_values_and_gates_ram_only(data_dir):
    path = _smoke(data_dir, "r")
    code, table = lowend.run(path, _baseline(data_dir, None, None))
    assert code == 0 and "| latency_p50_s | 0.6 | n/a | n/a | SKIP (no baseline yet" in table
    assert lowend.run(_smoke(data_dir, "fat", rss=9000.0), _baseline(data_dir, None, None))[0] == 1


def test_bad_baseline_and_report_are_setup_errors(data_dir, capsys):
    bad = data_dir / "bad.json"
    bad.write_text(json.dumps({"model": "x"}), "utf-8")
    assert main(["--lowend-gate", str(_smoke(data_dir, "r")), "--lowend-baseline", str(bad)]) == 2
    assert "missing" in capsys.readouterr().err
    assert main(["--lowend-gate", str(data_dir / "nope.json"), "--lowend-baseline", str(_baseline(data_dir, 1, 1))]) == 2


def test_committed_baseline_is_valid():
    b = lowend.validate_baseline(json.loads((ROOT / "eval/lowend_baseline.json").read_text("utf-8")))
    assert b["device"] == "cpu" and b["threads"] == 4


def test_latency_jsonl_one_line_per_clip(data_dir):
    jl = data_dir / "lat.jsonl"
    _smoke(data_dir, "r", extra_args=("--latency-jsonl", str(jl)))
    lines = [json.loads(x) for x in jl.read_text("utf-8").splitlines()]
    assert len(lines) == 20 and lines[0] == {"pair": "en->zh", "id": "en_0", "total": 0.6}


# ---- the workflow's path-filter step, executed (not grepped)

yaml = pytest.importorskip("yaml")
GIT, BASH = shutil.which("git"), shutil.which("bash")


def _decide(tmp_path, changed, base_ref="dev", head_ref="fix/1"):
    job = yaml.safe_load((ROOT / ".github/workflows/eval.yml").read_text("utf-8"))["jobs"]["low-end-cpu"]
    assert job["env"]["TLWU_CPU_THREADS"] == "4"
    script = next(s for s in job["steps"] if s.get("id") == "paths")["run"]
    script = (script.replace("${{ github.event_name }}", "pull_request").replace("${{ github.base_ref }}", base_ref)
              .replace("${{ github.head_ref }}", head_ref))
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(tmp_path), "PATH": __import__("os").environ["PATH"]}
    g = lambda *a: subprocess.run([GIT, *a], cwd=repo, env=env, check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    (repo / "base.txt").write_text("base")
    g("add", "-A")
    g("commit", "-qm", "base")
    for f in changed:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    g("add", "-A")
    g("commit", "-qm", "pr")
    out = tmp_path / "out"
    subprocess.run([BASH, "-e", "-c", script], cwd=repo, check=True, capture_output=True,
                   env={**env, "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(tmp_path / "sum")})
    return re.search(r"^run=(\w+)$", out.read_text(), re.M).group(1)


@pytest.mark.skipif(GIT is None or BASH is None, reason="git and bash are needed to run the step")
def test_workflow_runs_on_code_prs_and_always_on_the_release_pr(tmp_path):
    assert _decide(tmp_path / "a", ["tinglongwu/asr.py"]) == "true"
    assert _decide(tmp_path / "b", ["docs/x.md"]) == "false"
    assert _decide(tmp_path / "c", ["docs/x.md"], base_ref="main", head_ref="dev") == "true"  # release PR
    assert _decide(tmp_path / "d", ["docs/x.md"], base_ref="main", head_ref="docs/fastlane-1") == "false"
    on = yaml.safe_load((ROOT / ".github/workflows/eval.yml").read_text("utf-8"))[True]
    assert on["pull_request"]["branches"] == ["dev", "main"]
