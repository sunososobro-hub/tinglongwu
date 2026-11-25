import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collision_ledger as cl  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "collision_ledger" / "week.json"
DATA = json.loads(FIX.read_text(encoding="utf-8"))


def test_file_rows():
    rows = {r["file"]: r for r in cl.file_rows(DATA["prs"])}
    # a.py: PR1+PR2 open together (1 pair); PR3 opened later -> no extra pair
    assert rows == {
        "src/a.py": {"file": "src/a.py", "prs": 3, "pairs": 1, "dirty": 3},
        "src/b.py": {"file": "src/b.py", "prs": 1, "pairs": 0, "dirty": 1},
        "docs/x.md": {"file": "docs/x.md", "prs": 1, "pairs": 0, "dirty": 0},
    }
    assert cl.file_rows(DATA["prs"])[0]["file"] == "src/a.py"


def test_top_10_cap():
    prs = [{"number": i, "head": "fix/1", "created_at": "2026-11-24T00:00:00Z", "merged_at": "2026-11-24T01:00:00Z",
            "files": [f"f{i}.py"]} for i in range(15)]
    assert len(cl.file_rows(prs)) == 10


def test_push_causes_redo_p50():
    assert cl.push_causes(DATA["prs"]) == {"review": 1, "red test": 1, "conflict": 1}
    assert cl.redo_count(DATA["prs"]) == 1
    assert cl.test_p50(DATA["prs"]) == 20
    assert cl.test_p50([]) is None


def test_classify_push():
    assert cl.classify_push("Merge branch 'dev' into fix/1", False) == "conflict"
    assert cl.classify_push("fix it", True) == "red test"
    assert cl.classify_push("address review", False) == "review"


def test_render_and_cli():
    text = cl.render(DATA)
    assert "| src/a.py | 3 | 1 | 3 |" in text
    assert "| dev-red (failed test runs on dev) | 2 |" in text
    assert "| test p50 minutes | 20.0 |" in text
    out = subprocess.run([sys.executable, str(Path(cl.__file__)), "--fixture", str(FIX)],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == text


def test_script_size():
    assert len(Path(cl.__file__).read_text(encoding="utf-8").splitlines()) <= 300


def test_red_at_ignores_later_rerun():
    runs = [{"completed_at": "2026-11-24T09:05:00Z", "conclusion": "failure"},
            {"completed_at": "2026-11-24T09:20:00Z", "conclusion": "success"}]
    assert cl.red_at(runs, cl._ts("2026-11-24T09:10:00Z")) is True   # push B at 09:10 followed a red A
    assert cl.red_at(runs, cl._ts("2026-11-24T09:30:00Z")) is False  # after the rerun it was green
    assert cl.red_at(runs, cl._ts("2026-11-24T09:00:00Z")) is False  # nothing known yet
    assert cl.red_at([], cl._ts("2026-11-24T09:00:00Z")) is False


def test_dirty_requeues_matches_board_scan_text_and_pr():
    cs = [{"body": "requeue: PR #5 is dirty; next worker fixes it on the same branch"},
          {"body": "requeue: PR #6 is dirty; next worker fixes it on the same branch"},
          {"body": "requeue: PR #5 is test red; next worker fixes it on the same branch"},
          {"body": "requeue: PR #55 is dirty; x"}, {"body": "doing"}]
    assert cl.dirty_requeues(cs, 5) == 1
