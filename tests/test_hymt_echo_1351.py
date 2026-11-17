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
