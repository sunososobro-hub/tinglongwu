"""#1815: a PR that touches the shared MT data or the desktop MT text logic starts mobile-apk (flutter test incl. the
shared golden runner); a dev push of desktop-only code does not rebuild the phone APK.

The workflow's own path filters are evaluated with GitHub's glob rules (`**` crosses `/`, `*` does not)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
WF = Path(__file__).resolve().parent.parent / ".github/workflows/mobile-apk.yml"


def _on():
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return doc.get("on", doc.get(True))  # PyYAML reads the bare key `on` as True


def _glob_re(pat: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return re.compile(out + r"\Z")


def _triggers(event: str, changed: list[str]) -> bool:
    pats = _on()[event]["paths"]
    return any(_glob_re(p).match(f) for f in changed for p in pats)


@pytest.mark.parametrize("changed", [
    ["src/core/tinglongwu/translate.py"],
    ["src/core/tinglongwu/hymt.py"],
    ["src/core/tinglongwu/engines.py"],
    ["src/core/tinglongwu/repeat.py"],
    ["shared/golden/clean.jsonl"],
    ["mobile/lib/main.dart"],
])
def test_pr_touching_mt_or_shared_starts_mobile_apk(changed):
    assert _triggers("pull_request", changed)


@pytest.mark.parametrize("changed", [
    ["src/core/tinglongwu/core.py"],
    ["src/ui/tinglongwu/translate.py"],
    ["docs/mobile.md"],
    ["tests/test_shared_golden.py"],
])
def test_pr_without_mt_or_phone_files_does_not(changed):
    assert not _triggers("pull_request", changed)


def test_dev_push_of_desktop_mt_does_not_rebuild_the_apk():
    assert not _triggers("push", ["src/core/tinglongwu/translate.py"])
    assert _triggers("push", ["mobile/lib/main.dart"])
