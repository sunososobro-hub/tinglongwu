"""#1579: CodeRabbit reviews a PR once (no incremental re-review on every fix push)."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_no_incremental_review():
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load((ROOT / ".coderabbit.yaml").read_text("utf-8"))
    auto = cfg["reviews"]["auto_review"]
    assert auto["enabled"] is True
    assert auto["auto_incremental_review"] is False
