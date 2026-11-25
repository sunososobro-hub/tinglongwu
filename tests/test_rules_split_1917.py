"""#1917: .agent/tlwu_rules.md is an index; the sections live in .agent/rules/*.md.
Loading the index plus its files must give the same rule set as the old single file."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rules_text", ROOT / "scripts" / "rules_text.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

# Section headings of .agent/tlwu_rules.md before the split (dev 1c029ab).
OLD_HEADINGS = [
    "1. Who does what", "2. Locking", "3. Reviews and hard stops", "4. Model choice",
    "6. Throughput", "7. Merge conflicts: resolve in place",
    "8. Overlapping files: the author merges dev", "10. Throttle, picker, requeue, usage line",
    "13. Infra cap", "14. Merge safety: shared definitions", "15. Issue format",
    "17. dev merge gate", "18. Repo code never changes a contributor's machine",
    "20. Timing checks",
]
OLD_SUBHEADINGS = ["Throttle", "Picker (every worker)", "Requeue", "Usage line",
                   "Definitions", "Protected paths", "Base"]


def test_concatenated_rules_keep_every_old_section_in_order():
    text = rt.load()
    assert rt.headings(text) == OLD_HEADINGS
    assert [h for h in text.splitlines() if h.startswith("### ")] == ["### " + s for s in OLD_SUBHEADINGS]


def test_every_section_file_is_listed_once_and_holds_one_section():
    listed = rt.section_files()
    on_disk = sorted((ROOT / ".agent" / "rules").glob("*.md"))
    assert sorted(listed) == on_disk and len(set(listed)) == len(listed)
    for p in listed:
        assert len(rt.headings(p.read_text(encoding="utf-8"))) == 1, p.name


def test_index_keeps_no_section_text():
    assert rt.headings(rt.INDEX.read_text(encoding="utf-8")) == []


def test_load_fails_loudly_on_missing_section_file(tmp_path):
    (tmp_path / "idx.md").write_text("# x\n- @rules/01_a.md — 1. A\n")
    with pytest.raises(FileNotFoundError):
        rt.load(tmp_path / "idx.md")


def test_cli_prints_the_whole_rule_set():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "rules_text.py")],
                         capture_output=True, text=True, check=True).stdout
    assert rt.headings(out) == OLD_HEADINGS and "Allowed review authors" in out
