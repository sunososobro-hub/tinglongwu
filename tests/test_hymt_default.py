"""#66: Hy-MT2 is the default GPU translation engine (auto on CUDA), Qwen stays optional. No real models."""
import pytest

from tinglongwu import cli
from tinglongwu.hymt import HyMtTranslator, JaRouter, hymt_path, zh_script_guard
from tinglongwu.models import ModelNotFound
from tinglongwu.qwen import HYMT_MIN_FREE_MIB, choose_engine, model_path


class FakeLlm:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, **kw):
        self.calls.append(messages[0]["content"])
        return {"choices": [{"message": {"content": self.reply}}]}


class Base:
    """Stands in for the OPUS pivot."""

    def __init__(self):
        self.calls = []

    def preload(self, src, tgt):
        pass

    def translate(self, text, src, tgt):
        from tinglongwu.translate import Translation

        self.calls.append((text, src, tgt))
        return Translation(None, "opus:" + text)


def install(tmp_path, *names):
    for n in names:
        p = hymt_path(tmp_path) if n == "hymt2" else model_path(tmp_path, n)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
    return tmp_path


def test_auto_on_gpu_prefers_hymt_over_qwen(tmp_path):
    m = install(tmp_path, "hymt2", "qwen3-8b", "qwen3-4b")
    assert choose_engine("auto", "cuda", m, free_mib=lambda: 8000)[0] == "hymt2"  # 8B would fit, still Hy-MT2
    assert choose_engine("auto", "cuda", m, free_mib=lambda: HYMT_MIN_FREE_MIB)[0] == "hymt2"
    assert choose_engine("auto", "cuda", m, free_mib=lambda: HYMT_MIN_FREE_MIB - 1)[0] == "opus"
    assert choose_engine("auto", "cpu", m, free_mib=lambda: 99999)[0] == "opus"  # owner: CPU keeps OPUS


def test_auto_never_picks_qwen_and_says_how_to_get_hymt(tmp_path, capsys):
    m = install(tmp_path, "qwen3-8b", "qwen3-4b")  # Qwen on disk, a >= 8 GB GPU, but no Hy-MT2 (#276)
    name, why = choose_engine("auto", "cuda", m, free_mib=lambda: 9000)
    assert not name.startswith("qwen")
    assert name == "opus"
    assert "tlwu-setup --hymt" in capsys.readouterr().err
    assert choose_engine("qwen", "cuda", m)[0] == "qwen3-8b"  # explicit opt-in still works


def test_explicit_engines(tmp_path):
    m = install(tmp_path, "hymt2")
    assert cli.parse_args(["--engine", "hymt"]).engine == "hymt"
    assert choose_engine("hymt", "cpu", m)[0] == "hymt2"
    assert choose_engine("qwen", "cuda", m, free_mib=lambda: 8000)[0] == "qwen3-8b"  # still selectable


def test_explicit_hymt_without_model_is_a_setup_error(tmp_path):
    with pytest.raises(ModelNotFound, match="--hymt"):
        cli.make_translator("hymt", tmp_path, "tw", "cuda", say=lambda *a, **k: None)


def test_make_translator_hymt_translates_all_known_pairs_directly(tmp_path, monkeypatch):
    m = install(tmp_path, "hymt2")
    llm = FakeLlm("明天見。")
    monkeypatch.setattr("tinglongwu.hymt._load_llama", lambda path, gpu: llm)
    said = []
    tr = cli.make_translator("auto", m, "tw", "cuda", say=lambda msg, **k: said.append(msg),
                             choose=lambda e, d, md: choose_engine(e, d, md, free_mib=lambda: 6000))
    assert any("engine = hymt2" in s for s in said)
    assert tr.translate("Увидимся завтра, хорошо?", "ru", "zh").text == "明天見。"
    assert tr.translate("See you tomorrow, all right?", "en", "zh").text == "明天見。"
    assert len(llm.calls) == 2 and "Увидимся" in llm.calls[0]  # direct, no OPUS / English pivot


def test_make_translator_hymt_load_failure_is_loud_and_falls_back_to_opus(tmp_path, monkeypatch):
    m = install(tmp_path, "hymt2")

    def boom(path, gpu):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr("tinglongwu.hymt._load_llama", boom)
    said = []
    tr = cli.make_translator("hymt", m, "tw", "cuda", say=lambda msg, **k: said.append(msg))
    assert any("WARNING" in s and "CUDA out of memory" in s for s in said)
    assert any("engine = opus" in s for s in said)
    assert tr.hy is None and not tr._use_hy("ru", "zh")


def test_router_all_pairs_sends_unknown_language_to_base(tmp_path):
    m = install(tmp_path, "hymt2")
    llm, base = FakeLlm("你好"), Base()
    tr = JaRouter(base, m, "tw", "cpu", all_pairs=True,
                  factory=lambda *a: HyMtTranslator(m, "tw", "cpu", loader=lambda p, g: llm))
    assert tr.translate("Hello there", "en", "zh").text == "你好" and not base.calls
    assert tr.translate("Merhaba", "tr", "zh").text == "opus:Merhaba"  # Turkish is not in Hy-MT2's table here
    # default router (other engines): only Japanese pairs use Hy-MT2
    ja_only = JaRouter(base, m, "tw", "cpu", factory=lambda *a: HyMtTranslator(m, "tw", "cpu", loader=lambda p, g: llm))
    assert ja_only.translate("Hello there", "en", "zh").text == "opus:Hello there"


def test_short_line_left_in_kana_shows_source(tmp_path):
    m = install(tmp_path, "hymt2")
    tr = HyMtTranslator(m, "tw", "cpu", loader=lambda p, g: FakeLlm("ありがとうございます"))
    assert tr.translate("ありがとうございます", "ja", "zh").text == "ありがとうございます"
    assert zh_script_guard("Спасибо", "Спасибо。") == "Спасибо"
    assert zh_script_guard("Hey", "欸，齁") == "欸，齁"  # valid Taiwan interjections are kept
    long_src = "это довольно длинное предложение, которое модель перевела почти полностью"
    assert zh_script_guard(long_src, "這是一個相當長的句子 модель") == "這是一個相當長的句子 модель"


def test_hymt_download_ships_apache_notice(tmp_path, monkeypatch):
    from tinglongwu_setup import downloader

    monkeypatch.setattr(downloader, "get_gguf", lambda *a, **k: None)
    downloader.get_hymt({}, tmp_path)
    text = (tmp_path / downloader.HYMT_DIR / "NOTICE").read_text(encoding="utf-8")
    assert "Apache License, Version 2.0" in text and "Tencent" in text
