"""#30: OPUS ru->en->zh fixes - glossary `~` wrong renderings, '(c)' / '(英語：...)' artifacts, acronym protection."""
import pytest

from tinglongwu import cli, glossary
from tinglongwu.translate import PivotTranslator, protect_acronyms, restore_acronyms, zh_artifacts

TELECOM = "docs/examples/glossary_telecom_ru.txt"


def pivot(table, g=None):
    """Fake OPUS steps: every step looks its input up in `table`; records (src, tgt, input)."""
    calls = []

    def factory(s, t):
        def step(text):
            calls.append((s, t, text))
            return table[text]
        return step

    tr = PivotTranslator(None, "tw", step_factory=factory)
    tr.glossary = g
    return tr, calls


@pytest.fixture
def tg():
    return glossary.load(TELECOM)


def test_parse_wrong_forms():
    (e,) = glossary.parse("замирание | fading = 衰落 ~ 淡化 | 消散")
    assert (e.sources, e.target, e.wrong) == (["замирание", "fading"], "衰落", ["淡化", "消散"])
    (e,) = glossary.parse("роутер = 路由器")
    assert (e.target, e.wrong) == ("路由器", [])


def test_prompt_block_shows_target_without_wrong_forms(tg):
    b = tg.prompt_block("Это замирание сигнала")
    assert "замирание = 衰落." in b and "淡化" not in b


def test_fading_glossary_fix(tg):
    tr, _ = pivot({"Доплеровское замирание.": "Doppler fading.", "Doppler fading.": "多普勒淡化。"}, tg)
    assert tr.translate("Доплеровское замирание.", "ru", "zh").text == "多普勒衰落。"
    tr.glossary = None  # no glossary -> output unchanged (proves the test is red without the fix)
    assert tr.translate("Доплеровское замирание.", "ru", "zh").text == "多普勒淡化。"


def test_fix_only_when_term_occurs(tg):
    tr, _ = pivot({"Память тускнеет.": "Memory fades away.", "Memory fades away.": "記憶淡化。"}, tg)
    assert tr.translate("Память тускнеет.", "ru", "zh").text == "記憶淡化。"


def test_pivot_word_triggers_fix_and_simplified_output(tg):
    tr, _ = pivot({"Сигналы дефрагментированы.": "The signals are defragmented.",
                   "The signals are defragmented.": "信號是碎片整理的。"}, tg)
    tr.zh_variant = "cn"
    assert tr.translate("Сигналы дефрагментированы.", "ru", "zh").text == "信号是绕射的。"


def test_fluctuates_not_hesitates(tg):
    tr, _ = pivot({"Он колеблется.": "He hesitates.", "He hesitates.": "他猶豫不決。"}, tg)
    assert "起伏" in tr.translate("Он колеблется.", "ru", "zh").text


def test_copyright_artifact_stripped():
    tr, _ = pivot({"страны.": "countries.", "countries.": "(c) 各國。"})
    assert tr.translate("страны.", "ru", "zh").text == "各國。"
    assert zh_artifacts("© 各國", "страны") == "各國"
    assert zh_artifacts("(c) 公司", "(c) компания") == "(c) 公司"  # in the source: kept


def test_wiki_aside_stripped():
    tr, _ = pivot({"МНОЖЕСТВЕННЫЙ ВЫХОД": "MULTIPLE OUTPUT", "MULTIPLE OUTPUT": "多重輸出(英語:Multiple OutPUT)"})
    assert tr.translate("МНОЖЕСТВЕННЫЙ ВЫХОД", "ru", "zh").text == "多重輸出"
    assert zh_artifacts("多重輸出（英语：Multiple Output）。", "x") == "多重輸出。"


def test_acronym_protected_through_en_zh():
    table = {"Что такое MIMO?": "What is MIMO?", "What is MIMO?": "什麼是馬里奧？"}
    tr, calls = pivot(table)
    table["What is 9301?"] = "什麼是9301？"
    assert tr.translate("Что такое MIMO?", "ru", "zh").text == "什麼是MIMO？"
    assert ("en", "zh", "What is 9301?") in calls


def test_acronym_placeholder_lost_falls_back():
    table = {"What is MIMO?": "什麼是馬力？", "What is 9301?": "什麼是九千？"}
    tr, _ = pivot(table)
    assert tr.translate("What is MIMO?", "en", "zh").text == "什麼是馬力？"


def test_protect_acronyms_rules():
    assert protect_acronyms("MIMO and OFDM, MIMO.") == ("9301 and 9302, 9301.", {"9301": "MIMO", "9302": "OFDM"})
    assert protect_acronyms("MIMO OFDM link")[1] == {"9301": "MIMO", "9302": "OFDM"}
    assert protect_acronyms("MULTIPLE INPUT MULTIPLE OUTPUT")[1] == {}  # shouted words, not acronyms
    assert protect_acronyms("It is OK on TV, Wi-Fi 5 GHz")[1] == {}
    assert protect_acronyms("LTE costs 9301")[0] == "9302 costs 9301"
    assert restore_acronyms("A 9301 B", {"9301": "LTE"}) == "A LTE B"
    assert restore_acronyms("A 93011 B", {"9301": "LTE"}) is None


def test_cli_sets_glossary_on_opus_behind_ja_router(tmp_path, monkeypatch):
    from tinglongwu import hymt

    tr = cli.make_translator("opus", tmp_path, "tw", "cpu", say=lambda *a, **k: None,
                             choose=lambda e, d, m: ("opus", "test"))
    assert isinstance(tr, hymt.JaRouter) and hasattr(tr.base, "glossary")
