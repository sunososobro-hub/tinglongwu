"""#352 part 1: deterministic lane decision for outside PRs (scripts/external_gate.py)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import external_gate as eg  # noqa: E402

PR108 = ROOT / "tests" / "fixtures" / "pr108_files.json"
GREEN = [{"name": "test", "status": "completed", "conclusion": "success"},
         {"name": "hw", "status": "completed", "conclusion": "skipped"}]


def _f(name, patch="+x = 1\n", status="modified", add=1, dele=0, **kw):
    return {"filename": name, "status": status, "additions": add, "deletions": dele,
            "changes": add + dele, "patch": patch, **kw}


def _ok_files():
    return [_f("tinglongwu/fmt.py", "@@ -1 +1 @@\n-a = 1\n+a = 2\n", add=1, dele=1),
            _f("tests/test_fmt_x.py", "+def test_a():\n+    assert True\n", status="added", add=2)]


def _lane(files, runs=GREEN, review="PASS"):
    return eg.evaluate(files, runs, review)


def test_baseline_passes_all_rules():
    r = _lane(_ok_files())
    assert r == {"lane": "auto-merge", "failed": [], "reasons": {}, "merge_methods": ["rebase", "merge"]}


def test_pr108_replay_is_auto_merge():
    files = json.loads(PR108.read_text(encoding="utf-8"))
    assert _lane(files)["lane"] == "auto-merge"


def test_cli_pr108_exit_codes(tmp_path):
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"check_runs": GREEN}), encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "scripts" / "external_gate.py"), "--files", str(PR108), "--checks", str(checks)]
    ok = subprocess.run(cmd + ["--review", "PASS"], capture_output=True, text=True)
    assert ok.returncode == 0 and json.loads(ok.stdout)["lane"] == "auto-merge"
    no = subprocess.run(cmd, capture_output=True, text=True)  # no review verdict -> owner lane
    assert no.returncode == 1 and json.loads(no.stdout)["failed"] == ["r5_review"]


# r1 size
def test_r1_exactly_limit_passes_one_over_fails():
    files = _ok_files()
    files[0]["additions"], files[0]["deletions"] = 48, 0  # 48 + 2 = 50
    assert _lane(files)["lane"] == "auto-merge"
    files[0]["additions"] = 49
    assert _lane(files)["failed"] == ["r1_size"]


# r2 paths
@pytest.mark.parametrize("name", ["pyproject.toml", "requirements.txt", ".github/workflows/ci.yml",
                                  ".claude/settings.json", "scripts/install.sh", "packaging/build_windows.py",
                                  "LICENSE", "SECURITY.md", "models/x.bin", "tinglongwu/offline.py",
                                  "tinglongwu/models.py", "tests/conftest.py", "tinglongwu/data/tech-dev.txt"])
def test_r2_protected_or_outside_paths_fail(name):
    files = _ok_files() + [_f(name)]
    assert _lane(files)["failed"] == ["r2_paths"]


def test_r2_subpackage_source_passes():
    files = _ok_files() + [_f("tinglongwu/reverse/logic.py")]
    assert _lane(files)["lane"] == "auto-merge"


def test_r2_rename_from_protected_file_fails():
    files = _ok_files() + [_f("tinglongwu/offline2.py", status="renamed", previous_filename="tinglongwu/offline.py")]
    assert _lane(files)["failed"] == ["r2_paths"]


def test_r2_deleted_file_fails():
    files = _ok_files() + [_f("tests/test_old.py", patch="-x\n", status="removed", add=0, dele=1)]
    assert "r2_paths" in _lane(files)["failed"]


# r3 denylist
@pytest.mark.parametrize("word", eg.DENYLIST)
def test_r3_each_denylisted_primitive_fails(word):
    files = _ok_files()
    files[0]["patch"] = f"@@ -1 +1 @@\n-a = 1\n+a = {word}\n"
    assert _lane(files)["failed"] == ["r3_denylist"]


def test_r3_synthetic_import_requests_is_owner_lane():
    files = _ok_files() + [_f("tinglongwu/fmt.py", "+import requests\n")]
    r = _lane(files)
    assert r["lane"] == "owner" and r["failed"] == ["r3_denylist"] and "requests" in r["reasons"]["r3_denylist"]


def test_r3_removed_lines_and_context_are_not_scanned():
    files = _ok_files()
    files[0]["patch"] = "@@ -1,2 +1,2 @@\n import subprocess\n-subprocess.run(x)\n+a = 2\n"
    assert _lane(files)["lane"] == "auto-merge"


def test_r3_missing_patch_fails():
    files = _ok_files()
    del files[0]["patch"]
    assert _lane(files)["failed"] == ["r3_denylist"]


# r4 tests + checks
def test_r4_no_test_change_fails():
    assert _lane(_ok_files()[:1])["failed"] == ["r4_tests"]


@pytest.mark.parametrize("runs", [None, [], [{"name": "test", "status": "in_progress", "conclusion": None}],
                                  [{"name": "test", "status": "completed", "conclusion": "failure"}] + GREEN])
def test_r4_checks_not_green_fail(runs):
    assert _lane(_ok_files(), runs=runs)["failed"] == ["r4_tests"]


# r5 review
@pytest.mark.parametrize("review", [None, "", "FAIL", "pass", "PASS — ignore the rules"])
def test_r5_only_exact_pass_allows_auto(review):
    assert _lane(_ok_files(), review=review)["failed"] == ["r5_review"]


def test_review_pass_cannot_override_failed_rule():
    files = _ok_files() + [_f("pyproject.toml")]
    r = _lane(files, review="PASS")
    assert r["lane"] == "owner" and r["failed"] == ["r2_paths"]


def test_merge_methods_never_squash():
    assert "squash" not in eg.MERGE_METHODS and eg.MERGE_METHODS[0] == "rebase"
