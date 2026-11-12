"""#174: --glossary `~` wrong-rendering fixes also apply to Hy-MT2's zh output (fake model, no GGUF)."""
import pytest

from tinglongwu import cli, glossary
from tinglongwu.hymt import HyMtTranslator, JaRouter, hymt_path
from tinglongwu.engines import choose_engine

TELECOM = "docs/examples/glossary_telecom_ru.txt"


class FakeLlm:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, **kw):
        self.calls.append(messages[0]["content"])
        return {"choices": [{"message": {"content": self.reply}}]}


@pytest.fixture
def tg():
    return glossary.load(TELECOM)


def models(tmp_path):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    return tmp_path


def test_hymt_applies_glossary_fix_on_zh(tmp_path, tg):
    tr = HyMtTranslator(models(tmp_path), "tw", "cpu", loader=lambda p, g: FakeLlm("多普勒淡化。"))
    assert tr.translate("Доплеровское замирание.", "ru", "zh").text == "多普勒淡化。"  # no glossary: unchanged
    tr.glossary = tg
    assert tr.translate("Доплеровское замирание.", "ru", "zh").text == "多普勒衰落。"
    assert tr.translate("Память тускнеет.", "ru", "zh").text == "多普勒淡化。"  # term absent: no fix


def test_hymt_glossary_fix_in_simplified_output(tmp_path, tg):
    tr = HyMtTranslator(models(tmp_path), "cn", "cpu", loader=lambda p, g: FakeLlm("信号是碎片整理的。"))
    tr.glossary = tg
    assert tr.translate("Сигналы дифрагированные.", "ru", "zh").text == "信号是绕射的。"


def test_cli_glossary_reaches_hymt_engine(tmp_path, tg, monkeypatch):
    """The path `tlwu --glossary ... --engine auto` takes on a GPU: make_translator -> JaRouter(all_pairs)."""
    monkeypatch.setattr("tinglongwu.hymt._load_llama", lambda path, gpu: FakeLlm("多普勒淡化。"))
    tr = cli.make_translator("auto", models(tmp_path), "tw", "cuda", say=lambda *a, **k: None,
                             choose=lambda e, d, md: choose_engine(e, d, md, free_mib=lambda: 6000))
    assert isinstance(tr, JaRouter) and tr.hy is not None
    cli.apply_glossary(tr, tg)
    assert tr.hy.glossary is tg and tr.base.glossary is tg
    assert tr.translate("Доплеровское замирание.", "ru", "zh").text == "多普勒衰落。"


def test_router_glossary_reaches_opus_when_hymt_missing(tmp_path, tg):
    class Base:
        glossary = None

    tr = JaRouter(Base(), tmp_path, "tw", "cpu")
    assert tr.hy is None
    tr.glossary = tg
    assert tr.base.glossary is tg and tr.glossary is tg
