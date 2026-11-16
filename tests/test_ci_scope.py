"""#1221: docs-only and stats-site-only PRs get a short `test` job; everything else runs in full."""
from pathlib import Path

import ci_scope as cs


def test_readme_only_is_docs():
    assert cs.classify(["README.md", "README.zh-TW.md"]) == "docs"


def test_docs_dir_and_assets_are_docs():
    assert cs.classify(["docs/faq.md", "docs/img/latency_tiers.svg", "assets/mascot/tinglong.png"]) == "docs"


def test_support_json_is_full():
    # read by the app; not documentation
    assert cs.classify(["docs/support.json"]) == "full"


def test_agent_rules_and_workflows_are_full_even_as_md():
    assert cs.classify([".agent/tlwu_rules.md"]) == "full"
    assert cs.classify([".github/PULL_REQUEST_TEMPLATE.md"]) == "full"


def test_site_files_are_site():
    assert cs.classify(["scripts/stats_dashboard.py", "tests/test_stats_dashboard.py"]) == "site"
    assert cs.classify(["docs/roadmap.html", "README.md"]) == "site"


def test_any_code_file_makes_it_full():
    assert cs.classify(["README.md", "tinglongwu/cli.py"]) == "full"
    assert cs.classify(["scripts/stats_dashboard.py", "pyproject.toml"]) == "full"
    assert cs.classify([".github/workflows/ci.yml"]) == "full"


def test_empty_or_possibly_truncated_list_is_full():
    assert cs.classify([]) == "full"
    assert cs.classify([""]) == "full"
    assert cs.classify(["README.md"] * cs.MAX_LISTED_FILES) == "full"


def _tests_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tests"
    d.mkdir()
    (d / "test_mentions_readme.py").write_text('README = "README.md"\n', encoding="utf-8")
    (d / "test_other.py").write_text("x = 1\n", encoding="utf-8")
    (d / "test_stats_dashboard.py").write_text("y = 2\n", encoding="utf-8")
    return d


def test_docs_scope_runs_only_tests_that_name_the_file(tmp_path):
    d = _tests_dir(tmp_path)
    assert cs.select_tests("docs", ["README.md"], d) == [(d / "test_mentions_readme.py").as_posix()]
    assert cs.select_tests("docs", ["docs/faq.md"], d) == []


def test_doc_sweeping_tests_always_run_for_docs(tmp_path):
    d = _tests_dir(tmp_path)
    (d / "test_docs_paths.py").write_text("for md in ROOT.glob('*'): pass\n", encoding="utf-8")
    assert cs.select_tests("docs", ["docs/faq.md"], d) == [(d / "test_docs_paths.py").as_posix()]


def test_site_scope_adds_site_tests(tmp_path):
    d = _tests_dir(tmp_path)
    got = cs.select_tests("site", ["scripts/stats_dashboard.py", "README.md"], d)
    assert got == sorted([(d / "test_mentions_readme.py").as_posix(), (d / "test_stats_dashboard.py").as_posix()])


def test_full_scope_selects_nothing_extra(tmp_path):
    assert cs.select_tests("full", ["README.md"], _tests_dir(tmp_path)) == []


def test_cli_prints_github_output_lines(tmp_path, capsys):
    d = _tests_dir(tmp_path)
    f = tmp_path / "files.txt"
    f.write_text("README.md\n", encoding="utf-8")
    assert cs.main([str(f), "--tests-dir", str(d)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["scope=docs", "tests=" + (d / "test_mentions_readme.py").as_posix()]


def test_real_repo_site_tests_exist():
    # guards the glob list against renames: the stats page must keep at least its own tests in the site lane
    root = Path(__file__).resolve().parent
    assert any(root.glob("test_stats*.py"))
    assert any(root.glob("test_render_roadmap*.py"))


def test_editing_the_classifier_or_ci_workflow_is_always_full():
    # Opus review on #1223: a PR must not be able to relabel itself docs by editing the gate
    assert cs.classify(["scripts/ci_scope.py"]) == "full"
    assert cs.classify(["README.md", "scripts/ci_scope.py"]) == "full"
    assert cs.classify([".github/workflows/ci.yml", "docs/a.md"]) == "full"
    assert cs.classify(["scripts/stats_dashboard.py", "scripts/ci_scope.py"]) == "full"
