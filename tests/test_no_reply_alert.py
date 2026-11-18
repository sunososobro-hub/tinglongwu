import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import no_reply_alert as n  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def item(num, login, hours, pr=False, utype="User"):
    d = {"number": num, "title": f"t{num}", "html_url": f"https://x/{num}",
         "user": {"login": login, "type": utype},
         "created_at": (NOW - timedelta(hours=hours)).isoformat()}
    if pr:
        d["pull_request"] = {}
    return d


def human(login):
    return {"user": {"login": login, "type": "User"}}


def run(items, replies=None, merged=()):
    return [i["number"] for i in n.select_items(items, NOW, lambda it: (replies or {}).get(it["number"], []), set(merged))]


def test_bot_filter():
    items = [item(1, "coderabbitai", 100), item(2, "foo[bot]", 100), item(3, "x", 100, utype="Bot"),
             item(4, "claude-agent", 100), item(5, n.OWNER, 100), item(6, "alice", 100)]
    assert run(items) == [6]


def test_author_only_and_bot_replies_dont_count():
    items = [item(1, "alice", 60), item(2, "bob", 60), item(3, "carol", 60)]
    replies = {1: [human("alice")], 2: [{"user": {"login": "ci[bot]", "type": "Bot"}}], 3: [human(n.OWNER)]}
    assert run(items, replies) == [1, 2]


def test_thresholds():
    items = [item(1, "a", 47), item(2, "b", 49), item(3, "c", 25, pr=True), item(4, "d", 23, pr=True),
             item(5, "e", 25, pr=True)]
    assert run(items, merged=["e"]) == [2, 3]


def test_dedup_and_format():
    old = n.format_comment([{"number": 7, "title": "T", "author": "a", "hours": 50, "url": "u"}])
    assert n.alerted_numbers([old, "#9 unrelated line"]) == {7}
    sel = [{"number": 7}, {"number": 8}]
    assert n.new_items(sel, {7}) == [{"number": 8}]
    assert f"@{n.OWNER}" in old and "#7 `T` | a | 50 h | u" in old


def test_null_user_and_title_mentions():
    assert n.has_human_reply("a", [{"user": None}, human("bob")])
    assert not n.has_human_reply("a", [{"user": None}])
    assert n.new_items([{"number": 1}, {"number": 1}], set()) == [{"number": 1}]
    c = n.format_comment([{"number": 7, "title": "hi @bob", "author": "a", "hours": 50, "url": "u"}])
    assert "`hi @bob`" in c


def test_title_markdown_is_literal():
    for raw in ("[click](http://evil) <b>x</b>", "See www.example.com", "Expected &lt;tag&gt; @bob", "a`b"):
        t = n.plain_title(raw)
        assert t.startswith("`") and t.endswith("`") and t.count("`") == 2
        assert t[1:-1] == raw.replace("`", "'")
    c = n.format_comment([{"number": 7, "title": "See www.example.com", "author": "a", "hours": 50, "url": "https://x/7"}])
    assert "#7 `See www.example.com` | a | 50 h | https://x/7" in c
    assert n.alerted_numbers([c]) == {7}
