"""#1917: scripts/hot_files.py ranks files by PRs touching and by merge-from-dev commits changed on both sides."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hot_files as hf  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "hot_files" / "window.json"
DATA = json.loads(FIX.read_text(encoding="utf-8"))


def test_counts_dedupe_files_per_pr_and_per_merge():
    touched, merged = hf.counts(DATA["prs"])
    assert touched["tinglongwu/app.py"] == 3
    assert touched["scripts/x.py"] == 1  # listed twice in one PR -> one PR
    assert touched[".agent/tlwu_rules.md"] == 2
    assert merged["tinglongwu/app.py"] == 2 and merged[".agent/tlwu_rules.md"] == 1 and merged["scripts/x.py"] == 1
    assert merged["docs/b.md"] == 0


def test_top_orders_by_count_then_name_and_caps():
    touched, _ = hf.counts(DATA["prs"])
    assert hf.top(touched, 2) == [("tinglongwu/app.py", 3), (".agent/tlwu_rules.md", 2)]
    assert [f for f, _ in hf.top(touched, 15)][2:] == ["docs/a.md", "docs/b.md", "scripts/x.py"]
    many = [{"number": i, "files": [f"f{i:02d}.py"]} for i in range(20)]
    assert len(hf.top(hf.counts(many)[0], 15)) == 15


def test_both_sides_is_intersection():
    assert hf.both_sides(["a", "b", "c"], ["c", "a", "d"]) == ["a", "c"]
    assert hf.both_sides(["a"], []) == []


def test_render_and_cli():
    text = hf.render(DATA)
    assert "4 dev PRs: merged in the last 14 days + open; 3 merge-from-dev commits" in text
    assert "| 1 | tinglongwu/app.py | 3 | 2 |" in text  # by PRs: PRs, conflict merges
    assert "| 3 | scripts/x.py | 1 | 1 |" in text      # by conflict merges: ties broken by name
    out = subprocess.run([sys.executable, str(Path(hf.__file__)), "--fixture", str(FIX)],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == text


def test_render_without_merges():
    assert "| - | none | 0 | 0 |" in hf.render({"days": 14, "prs": [{"number": 1, "files": ["a"]}]})


def test_collect_counts_only_two_parent_commits(monkeypatch):
    """collect() keeps merged-in-window + open PRs and inspects only merge commits (two parents)."""
    now = hf._ts("2026-11-25T00:00:00Z")
    pages = {
        "pulls?state=closed": [{"number": 1, "merged_at": "2026-11-20T00:00:00Z", "updated_at": "2026-11-21T00:00:00Z"},
                               {"number": 2, "merged_at": None, "updated_at": "2026-11-20T00:00:00Z"},
                               {"number": 3, "merged_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-02T00:00:00Z"}],
        "pulls?state=open": [{"number": 9}],
        "pulls/1/files": [{"filename": "a.py"}], "pulls/9/files": [{"filename": "b.py"}],
        "pulls/1/commits": [{"sha": "c1", "parents": [{"sha": "p"}]},
                            {"sha": "m1", "parents": [{"sha": "p"}, {"sha": "d"}]}],
        "pulls/9/commits": [],
        "commits/m1": {"files": [{"filename": "a.py"}, {"filename": "z.py"}]},
        "compare/d...m1": {"files": [{"filename": "a.py"}]},
    }

    def fake(path):
        for k, v in pages.items():
            if k in path:
                return v
        raise AssertionError(path)

    monkeypatch.setattr(hf, "_gh", fake)
    data = hf.collect(now, 14)
    assert data == {"days": 14, "prs": [{"number": 1, "files": ["a.py"], "conflict_files": [["a.py"]]},
                                        {"number": 9, "files": ["b.py"], "conflict_files": []}]}


def test_post_comment_first_line(monkeypatch):
    calls = []
    monkeypatch.setattr(hf, "collect", lambda now, days: {"days": days, "prs": DATA["prs"]})
    monkeypatch.setattr(hf.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert hf.main(["--post", "1917"]) == 0
    assert len(calls) == 1 and calls[0][4] == "repos/sunososobro-hub/tinglongwu/issues/1917/comments"
    assert calls[0][-1].startswith("body=[hot files 14d]\n## hot files")
