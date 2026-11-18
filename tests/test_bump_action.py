"""#1472: bump cadence decision, generated what's-new section, and that the result passes bump_exempt."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bump_action as b
from release_notes import bump_exempt

TPE = timezone(timedelta(hours=8))


def at(h, m=0, day=18):
    return datetime(2026, 11, day, h, m, tzinfo=TPE)


BASE = dict(version="0.4.8", tags={"v0.4.8"}, ahead=3, test_green=True, open_bump_pr=False, hotfix_active=False,
            hotfix_backmerge_open=False, last_bump_at=at(5), p01_fix_since_release=False, throttle=2)


def d(now, **kw):
    return b.decide(now=now, **{**BASE, **kw})


def test_first_wake_after_11_opens():
    ok, why = d(at(11, 5))
    assert ok and "11:00" in why


def test_second_wake_after_17_opens_and_same_slot_does_not():
    assert d(at(17, 5), last_bump_at=at(11, 20))[0]
    assert not d(at(17, 5), last_bump_at=at(17, 1))[0]
    assert not d(at(12, 0), last_bump_at=at(11, 10))[0]


def test_each_blocking_condition():
    assert not d(at(11, 5), tags=set())[0]  # last bump not tagged yet
    assert not d(at(11, 5), ahead=0)[0]
    assert not d(at(11, 5), test_green=False)[0]
    assert not d(at(11, 5), open_bump_pr=True)[0]
    assert not d(at(11, 5), hotfix_active=True)[0]
    assert not d(at(11, 5), hotfix_backmerge_open=True)[0]
    assert not d(at(11, 5), throttle=0)[0]
    assert d(at(11, 5), throttle=None)[0]


def test_p01_fix_bypasses_the_slot_but_not_other_conditions():
    assert d(at(13, 0), last_bump_at=at(11, 30), p01_fix_since_release=True)[0]
    assert not d(at(13, 0), last_bump_at=at(11, 30), p01_fix_since_release=True, test_green=False)[0]


def test_slot_start_before_11_uses_yesterdays_17():
    assert b.slot_start(at(9, 0)) == at(17, 0, day=17)


def test_bullets_strip_numbers_and_skip_infra():
    out = b.bullets(["fix #1479: Mobile two-way ignores filler words (#1486)", "release: bump __version__ to 0.4.8",
                     "[ci] add sweep", "feat #12: Show active model"])
    assert out == ["- Mobile two-way ignores filler words.", "- Show active model."]
    assert b.bullets(["ci: x"]) == [b.FALLBACK_BULLET]
    assert all("#" not in x and "TODO" not in x for x in out)


def test_generated_files_pass_bump_exempt():
    init = Path(b.ROOT / "tinglongwu" / "__init__.py").read_text("utf-8")
    wn = Path(b.ROOT / b.WHATS_NEW_FILE).read_text("utf-8")
    ver = b.app_version(init)
    new = b.next_patch(ver)
    new_init, new_wn = b.bump_files(init, wn, new, ["- Faster subtitles."])
    import difflib

    def patch(old, cur):
        return "\n".join(list(difflib.unified_diff(old.split("\n"), cur.split("\n"), lineterm="", n=0))[2:])

    files = [{"filename": "tinglongwu/__init__.py", "status": "modified", "patch": patch(init, new_init)},
             {"filename": b.WHATS_NEW_FILE, "status": "modified", "patch": patch(wn, new_wn)}]
    ok, why = bump_exempt(files, new_wn)
    assert ok, why
    assert f"## v{new}\n- Faster subtitles.\n" in new_wn
    assert new_init.replace(new, ver) == init


def test_pr_body_has_required_lines():
    body = b.pr_body(7, "0.4.9", "why", ["- x."])
    assert "Owner comments:" in body and "Refs #7" in body
    assert body.splitlines()[-1].startswith("tlwu-usage:") and json.dumps(body)


def test_markdown_marker_titles_give_accepted_bullets_and_comment_after_heading_is_ok():
    out = b.bullets(["> Improve subtitle timing", "# Faster start", "1. Fix crash"])
    assert out == ["- Improve subtitle timing.", "- Faster start.", "- Fix crash."]
    assert b.bullets(["> CI: update runner image"]) == [b.FALLBACK_BULLET]
    assert b.bullets(["2FA support for sign-in", "1.5x faster start"]) == ["- 2FA support for sign-in.", "- 1.5x faster start."]
    assert b.bullets(["\u0662. Improve timing"]) == ["- \u0662. Improve timing."]
    fenced = "# N\n\n```\n## v9.9.9\n```\n\n## v0.4.8\n- old\n"
    assert b.bump_files('__version__ = "0.4.8"\n', fenced, "0.4.9", out)[1].index("## v0.4.9") < b.bump_files('__version__ = "0.4.8"\n', fenced, "0.4.9", out)[1].index("## v0.4.8")
    wn = "# What's new\n\n<!-- ex:\n## v1.2.3\n- x\n-->\n\n## v0.4.8\n- old\n\n<!-- later comment -->\n"
    _, new_wn = b.bump_files('__version__ = "0.4.8"\n', wn, "0.4.9", out)
    assert new_wn.index("## v0.4.9") < new_wn.index("## v0.4.8")
    assert all(x.startswith("- ") and x[2] not in "#>*+=<-" and not x[2].isdigit() for x in out)


def test_new_section_goes_after_fence_and_before_old_release():
    fenced = "# N\n\n```\n## v9.9.9\n```\n\n## v0.4.8\n- old\n"
    new = b.bump_files('__version__ = "0.4.8"\n', fenced, "0.4.9", ["- x."])[1]
    close = new.index("```", new.index("```") + 3)
    assert close < new.index("## v0.4.9") < new.index("## v0.4.8")
