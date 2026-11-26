"""#1799: the eval's --term-scenario loads the same built-in tables as a real ru session."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from tlwu_eval import __main__ as ev  # noqa: E402


def test_default_ru_loads_tech_tables_and_none_does_not():
    ev._TERMS.clear()
    off = ev.term_glossary("none", "ru", "zh")
    on = ev.term_glossary("", "ru", "zh")
    assert len(on.entries) > len(off.entries) + 1000
    assert on.relevant("Удали старую ветку.")  # ветка -> 分支 fires with the default
    assert not off.relevant("Удали старую ветку.")


def test_no_scenario_means_no_tables():
    ev._TERMS.clear()
    assert ev.term_glossary(None, "ru", "zh") is None
