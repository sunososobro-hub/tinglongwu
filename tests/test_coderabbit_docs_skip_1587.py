"""#1587: CodeRabbit skips README / docs Markdown, still reviews code and the rules file."""
from fnmatch import fnmatch
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_docs_markdown_excluded():
    yaml = pytest.importorskip("yaml")
    filters = yaml.safe_load((ROOT / ".coderabbit.yaml").read_text("utf-8"))["reviews"]["path_filters"]
    excluded = [f[1:] for f in filters if f.startswith("!")]
    assert "README*.md" in excluded and "docs/**/*.md" in excluded

    def skipped(path):
        return any(fnmatch(path, pat) for pat in excluded)
    assert skipped("README.md") and skipped("README.zh-TW.md") and skipped("docs/guide/setup.md")
    assert not skipped(".agent/tlwu_rules.md") and not skipped("tinglongwu/gui/app.py")
    assert not skipped("docs/support.json")
