"""#1351: Hy-MT2 output that starts with the card prompt's instruction line (eval-flores run 44013625413, ru->en)
loses that line; real translations are unchanged (fake model, no GGUF)."""
import pytest

from tinglongwu.engines import _SHARED
from tinglongwu.hymt import HyMtTranslator, hymt_path, strip_echo

NOTE = "Note that you should only output the translated result without any additional explanation:"


class FakeLlm:
    def __init__(self, reply):
        self.reply = reply

    def create_chat_completion(self, messages, **kw):
        return {"choices": [{"message": {"content": self.reply}}]}


def translate(tmp_path, reply, src="ru", tgt="en"):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    _SHARED.clear()  # one fake model per test, not the instance an earlier test loaded for this path
    tr = HyMtTranslator(tmp_path, "tw", "cpu", loader=lambda path, gpu: FakeLlm(reply))
    return tr.translate("Вода выливается за плотину.", src, tgt).text


@pytest.mark.parametrize("reply", [
    f"{NOTE}\n\nWater is spilling over the levee.",
    f"{NOTE} Water is spilling over the levee.",
    "It should be noted that only the translated result should be output, without any additional explanation: "
    "Water is spilling over the levee.",
    "Please note that you should only output the translated text without any additional explanations.\n"
    "Water is spilling over the levee.",
])
def test_echoed_instruction_is_removed_from_english_output(tmp_path, reply):
    assert translate(tmp_path, reply) == "Water is spilling over the levee."


def test_echoed_chinese_instruction_is_removed(tmp_path):
    out = translate(tmp_path, "注意只需要输出翻译后的结果，不要额外解释：\n\n大坝很高。", tgt="zh")
    assert out == "大壩很高。"  # OpenCC tw after the echo is gone


@pytest.mark.parametrize("text", [
    "Note that the dam was built in 1950.",
    "Water is spilling over the levee.",
    "The translated result was published without any explanation.",  # no "note that" instruction opener
    "You should read the translated text without any explanation first.",  # under a sentence, no ":" echo end
    "注意安全，不要靠近大壩。",
    "Note that the translated text contains an explanation.",  # no "only ... without explanation" directive
    "只需要输出额外解释即可。",  # no 翻译 in it: not the card instruction
    "Note that only the translated text has no explanation.",  # no "output" directive
    "You should only read the translated text. No explanation.",  # never matches across a sentence end
])
def test_real_translations_are_unchanged(text):
    assert strip_echo(text) == text


def test_echo_only_output_becomes_empty():
    assert strip_echo(NOTE) == ""


def test_contextual_instruction_echo_is_removed():
    from tinglongwu.hymt import build_prompt

    instr = build_prompt("x", "ru", "zh", context="prev").split("\n")[1]  # the instruction line the model may repeat
    assert strip_echo(f"{instr}\n大坝很高。") == "大坝很高。"
    assert strip_echo("参考上面的信息很有用。") == "参考上面的信息很有用。"


# #1615: the instruction echoed in the TARGET language (phone field test 2026-11-19, auto -> ru)
RU_ECHO = ("Примите во внимание, что следует выводить только переведенный результат, "
           "без каких-либо дополнительных комментариев")


@pytest.mark.parametrize("echo", [
    RU_ECHO,
    RU_ECHO + ".",
    "Обратите внимание: выводите только переведённый текст без объяснений.",
    "Bitte nur das übersetzte Ergebnis ohne zusätzliche Erklärungen ausgeben.",
    "Notez qu'il faut uniquement afficher le résultat traduit, sans aucune explication.",
    "Tenga en cuenta que solo debe mostrar el resultado traducido sin ninguna explicación adicional.",
    "翻訳結果のみを出力し、追加の説明は不要です。",
    "추가 설명 없이 번역된 결과만 출력하세요.",
])
def test_target_language_echo_only_becomes_empty(echo):
    assert strip_echo(echo) == ""


@pytest.mark.parametrize("echo,sep", [(RU_ECHO, ": "), (RU_ECHO, ".\n"), (RU_ECHO + ".", " ")])
def test_target_language_echo_then_translation_keeps_translation(echo, sep):
    assert strip_echo(f"{echo}{sep}Давайте поделимся.") == "Давайте поделимся."


def test_target_language_echo_through_translator(tmp_path):
    # desktop clean_output joins lines; the echo still goes and the translation stays
    assert translate(tmp_path, f"{RU_ECHO}:\n\nВода переливается через дамбу.", src="en", tgt="ru") == \
        "Вода переливается через дамбу."
    assert translate(tmp_path, RU_ECHO, src="en", tgt="ru") == ""


@pytest.mark.parametrize("text", [
    "Здравствуйте.",
    "Давайте поделимся только результатом, без лишних слов.",  # no translat- part
    "Переводчик ждёт результат без комментариев. Только завтра.",  # parts split across sentences
    "Je ne parle que français, sans traduction.",  # no result/text, no explanation
    "Solo el texto traducido llegó, sin firma.",  # no explanation/comment part
    "この翻訳だけで十分です。",
    "번역만 하면 됩니다.",
])
def test_target_language_real_translations_are_unchanged(text):
    assert strip_echo(text) == text


def test_unpunctuated_echo_then_translation_keeps_translation(tmp_path):
    # CodeRabbit on #1619: clean_output joins the lines, so the echo is stripped from the raw text first
    out = translate(tmp_path, f"{RU_ECHO}\n\nВода переливается через дамбу.", src="en", tgt="ru")
    assert out == "Вода переливается через дамбу."
    en = translate(tmp_path, f"{NOTE[:-1]}\nWater is spilling over the levee.")
    assert en == "Water is spilling over the levee."


def test_unpunctuated_echo_only_becomes_empty():
    assert strip_echo(NOTE[:-1]) == ""
    assert strip_echo("注意只需要输出翻译后的结果，不要额外解释") == ""


def test_russian_sentence_about_a_translator_is_kept():
    text = "Только переводчик получил результат без комментариев."
    assert strip_echo(text) == text
