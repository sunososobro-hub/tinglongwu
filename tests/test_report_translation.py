"""#677: 'Wrong translation' builds a prefilled issue URL; nothing is opened unless the user confirms."""
from urllib.parse import parse_qs, urlparse

import pytest

from tinglongwu.report_translation import MAX_URL, build_url, report_line


def _qs(url):
    u = urlparse(url)
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


@pytest.mark.parametrize("src,out", [("今日は晴れです", "今天天氣晴朗"), ("Привет, мир", "你好，世界"), ("你好", "Hello there")])
def test_url_encodes_zh_ja_ru(src, out):
    url, happened = build_url("v0.3.5", "listen", "ja -> zh-TW", src, out, os_="Linux")
    assert url.isascii()
    path, q = _qs(url)
    assert path == "/sunososobro-hub/tinglongwu/issues/new"
    assert q["template"] == "translation-error.yml"
    assert (q["version"], q["os"], q["mode"], q["langpair"]) == ("v0.3.5", "Linux", "listen", "ja -> zh-TW")
    assert q["happened"] == happened
    assert src in q["happened"] and out in q["happened"]


def test_truncation_keeps_url_short_and_characters_whole():
    out = "翻" * 5000
    url, happened = build_url("v1", "listen", "ja -> zh-TW", "原文", out, os_="Windows")
    assert len(url) <= MAX_URL
    assert happened.endswith("…") and "原文" in happened
    assert _qs(url)[1]["happened"] == happened  # decodes cleanly: no split UTF-8 sequence


def test_short_text_not_truncated():
    _, happened = build_url("v1", "listen", "en -> zh-TW", "hi", "你好", os_="macOS")
    assert "…" not in happened


def test_unknown_mode_falls_back_to_listen():
    assert _qs(build_url("v1", "two-way", "en -> zh-TW", "a", "b", os_="Linux")[0])[1]["mode"] == "listen"


def test_cancel_opens_nothing():
    opened, shown = [], []
    ok = report_line("hello", "你好", version="v1", mode="listen", langpair="en -> zh-TW",
                     confirm=lambda t: shown.append(t) or False, open_url=opened.append)
    assert ok is False and opened == []
    assert shown == ["Source: hello\ntlwu: 你好"]  # the dialog shows exactly what is prefilled


def test_confirm_opens_once_and_empty_line_does_nothing():
    opened = []
    assert report_line("hello", "你好", version="v1", mode="listen", langpair="x", confirm=lambda t: True, open_url=lambda u: opened.append(u) or True)
    assert len(opened) == 1 and opened[0].startswith("https://github.com/")
    assert not report_line("", " ", version="v1", mode="listen", langpair="x", confirm=lambda t: True, open_url=opened.append)
    assert len(opened) == 1
