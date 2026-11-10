import datetime
import io

from tinglongwu.sink import Result, TextSink
from tinglongwu.translate import PivotTranslator, zh_convert

I = " " * 16


def out(r):
    s = io.StringIO()
    TextSink(s, now=lambda: datetime.datetime(2026, 1, 1, 12, 3, 10)).write(r)
    return s.getvalue()


def test_format_ru():
    assert out(Result("ru", "привет", "hello", "你好")) == (
        f"12:03:10 ◀ Them привет\n{I}→ [EN] hello\n{I}→ [ZH] 你好\n\n")


def test_format_en_only_en_zh():
    assert out(Result("en", "hello", None, "你好")) == f"12:03:10 ◀ Them hello\n{I}→ [ZH] 你好\n\n"


def test_zh_variants():
    assert zh_convert("软件", "tw") == "軟體"
    assert zh_convert("軟體", "cn") == "软体"


def test_pivot_skips_en_step_and_variant():
    calls = []

    def factory(s, t):
        calls.append((s, t))
        return lambda x: "软件"

    tw = PivotTranslator(None, "tw", factory).translate("hi", "en", "zh")
    assert calls == [("en", "zh")] and tw.pivot is None and tw.text == "軟體"
    cn = PivotTranslator(None, "cn", factory).translate("hi", "ru", "zh")
    assert calls[1:] == [("ru", "en"), ("en", "zh")] and cn.text == "软件" and cn.pivot


def test_format_ja_ko_and_en_target():
    assert out(Result("ja", "こんにちは", "hello", "안녕하세요", "ko")) == \
        f"12:03:10 ◀ Them こんにちは\n{I}→ [EN] hello\n{I}→ [KO] 안녕하세요\n\n"
    assert out(Result("ru", "привет", None, "hello", "en")) == f"12:03:10 ◀ Them привет\n{I}→ [EN] hello\n\n"


def test_labels_per_ui_lang_and_me_side(monkeypatch):
    from tinglongwu import fmt

    now = datetime.datetime(2026, 1, 1, 12, 3, 10)
    monkeypatch.setenv("TLWU_UI_LANG", "zh-TW")
    assert fmt.block("me", "我覺得好笑", [(None, "Смешно")], now) == f"12:03:10 ▶ 我 我覺得好笑\n{I}→ Смешно\n\n"
    assert fmt.block("them", "Хорошо", [("ZH", "很好")], now).startswith("12:03:10 ◀ 對方 Хорошо\n")
    monkeypatch.setenv("TLWU_UI_LANG", "ru")
    assert "▶ Я " in fmt.block("me", "x", (), now) and "◀ Собеседник " in fmt.block("them", "x", (), now)
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    assert "▶ Me " in fmt.block("me", "x", (), now)


def test_color_only_on_tty_without_no_color(monkeypatch):
    from tinglongwu import fmt

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert fmt.color_enabled(Tty()) and not fmt.color_enabled(io.StringIO())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not fmt.color_enabled(Tty())
    now = datetime.datetime(2026, 1, 1, 0, 0, 0)
    assert "\033[32m▶ Me\033[0m" in fmt.block("me", "x", (), now, color=True)
    assert "\033[36m◀ Them\033[0m" in fmt.block("them", "x", (), now, color=True)
