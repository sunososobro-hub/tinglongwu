"""#43: OPUS sometimes emit ASS/SSA subtitle style codes such as {\fn..\fs..} in zh output, followed by a
sentence that has nothing to do with the source (the next line of a bilingual subtitle file). They are stripped;
the unrelated tail is dropped; genuine lines (also English-only speech) pass unchanged."""
from __future__ import annotations

import pytest

from tinglongwu.translate import PivotTranslator, strip_subtitle_codes, zh_artifacts


def pivot(table):
    """Fake OPUS steps (src, tgt) -> lookup in `table`, like the real step factory's callables."""
    return PivotTranslator(None, "tw", step_factory=lambda s, t: (lambda text: table[text]))


def test_style_code_and_unrelated_tail_dropped_through_the_pivot():
    tr = pivot({"Так себе.": "So-so.", "So-so.": "馬馬虎虎。{\\fn微軟雅黑\\fs14}我們走吧，船長在等。"})
    t = tr.translate("Так себе.", "ru", "zh")
    assert t.text == "馬馬虎虎。" and t.pivot == "So-so."


def test_leading_style_code_keeps_the_translation():
    tr = pivot({"Hello, everyone.": "{\\an8}{\\fnArial\\fs16}大家好。"})
    assert tr.translate("Hello, everyone.", "en", "zh").text == "大家好。"


def test_only_codes_left_shows_the_source_line():
    tr = pivot({"Mm-hmm.": "{\\fnArial\\fs16\\c&H00FFFF&}"})
    assert tr.translate("Mm-hmm.", "en", "zh").text == "Mm-hmm."


def test_genuine_lines_are_untouched():
    tr = pivot({"Let's go to the station.": "我們去車站吧。"})
    assert tr.translate("Let's go to the station.", "en", "zh").text == "我們去車站吧。"
    # English speech heard in zh mode (the line shows [ZH] only): translation passes unchanged, nothing filtered
    assert tr.translate("Let's go to the station.", "en", "zh").pivot is None
    assert zh_artifacts("我們去車站吧。", "Let's go to the station.") == "我們去車站吧。"


def test_codes_in_the_source_are_kept():
    src = "Type {\\b1}bold{\\b0} here"
    assert strip_subtitle_codes("在此輸入{\\b1}粗體{\\b0}", src) == "在此輸入{\\b1}粗體{\\b0}"


@pytest.mark.parametrize("raw,want", [
    ("{\\an8}你好\\N再見", "你好 再見"),           # line-break escape after a leading block
    ("你好\\N{\\fs12}別的句子", "你好"),            # block mid-line: tail dropped, escape before it tidied
    ("{\\i1}{\\i0}", ""),                       # nothing but codes
    ("普通的{句子}", "普通的{句子}"),               # plain braces are not style codes
])
def test_strip_subtitle_codes(raw, want):
    assert strip_subtitle_codes(raw, "source") == want
