import io

from tinglongwu.sink import Result, TextSink
from tinglongwu.translate import PivotTranslator, zh_convert


def out(r):
    s = io.StringIO()
    TextSink(s).write(r)
    return s.getvalue()


def test_format_ru():
    assert out(Result("ru", "привет", "hello", "你好")) == "[RU] привет\n[EN] hello\n[ZH] 你好\n\n"


def test_format_en_only_en_zh():
    assert out(Result("en", "hello", None, "你好")) == "[EN] hello\n[ZH] 你好\n\n"


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
        "[JA] こんにちは\n[EN] hello\n[KO] 안녕하세요\n\n"
    assert out(Result("ru", "привет", None, "hello", "en")) == "[RU] привет\n[EN] hello\n\n"
