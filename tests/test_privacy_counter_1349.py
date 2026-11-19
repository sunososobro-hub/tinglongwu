"""#1349: PRIVACY.md must describe the update counter as soon as COUNTER_BASE is set."""
from pathlib import Path

from tinglongwu_setup import selfupdate

ROOT = Path(__file__).resolve().parents[1]


def _counter_row_ok(text: str) -> bool:
    return "COUNTER_BASE" in text and ("no install ID" in text or "沒有安裝 ID" in text)


def test_privacy_lists_counter_when_enabled():
    if not selfupdate.COUNTER_BASE:
        return  # off: the row still has to exist, checked below
    for name in ("docs/PRIVACY_POLICY.md", "docs/PRIVACY_POLICY.zh-TW.md"):
        assert _counter_row_ok((ROOT / name).read_text("utf-8")), name


def test_privacy_counter_row_present_and_fields_named():
    en = (ROOT / "docs" / "PRIVACY_POLICY.md").read_text("utf-8")
    zh = (ROOT / "docs" / "PRIVACY_POLICY.zh-TW.md").read_text("utf-8")
    assert "COUNTER_BASE" in en and "no install ID" in en and "version" in en
    assert "COUNTER_BASE" in zh and "沒有安裝 ID" in zh


def test_privacy_missing_row_detected():
    assert not _counter_row_ok("| 4b | update check |")
