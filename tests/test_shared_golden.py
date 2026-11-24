"""Shared MT golden cases (#1831, parent #1617): current desktop outputs of the text cleaning and prompt code.
Cases tagged `known_diff` are ones where the phone is known to differ (part 1b); the desktop must still match."""
import json
from pathlib import Path

import pytest

from tinglongwu.engines import clean_output
from tinglongwu.hymt import build_prompt, strip_echo

GOLDEN = Path(__file__).resolve().parent.parent / "shared" / "golden"


def _load(name):
    with open(GOLDEN / f"{name}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


CLEAN = _load("clean")
PROMPT = _load("prompt")


def test_golden_sizes():
    assert len(CLEAN) >= 20 and len(PROMPT) >= 10


@pytest.mark.parametrize("case", CLEAN, ids=lambda c: repr(c["input"])[:40])
def test_clean_golden(case):
    assert clean_output(strip_echo(case["input"])) == case["expected"]


@pytest.mark.parametrize("case", PROMPT, ids=lambda c: f"{c['src']}-{c['tgt']}-{c['text'][:10]}")
def test_prompt_golden(case):
    terms = [tuple(t) for t in case["terms"]] if case.get("terms") else None
    assert build_prompt(case["text"], case["src"], case["tgt"], terms, case.get("context")) == case["expected"]


def test_known_diff_summary(capsys):
    n_clean = sum(1 for c in CLEAN if c.get("known_diff"))
    n_prompt = sum(1 for c in PROMPT if c.get("known_diff"))
    with capsys.disabled():
        print(f"\nknown_diff: clean={n_clean} prompt={n_prompt}")
