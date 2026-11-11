import pytest

from tinglongwu import cli
from tinglongwu import qwen
from tinglongwu.models import ModelNotFound
from tinglongwu.qwen import (QWEN4B_MIN_FREE_MIB, QWEN8B_MIN_FREE_MIB, QwenTranslator, build_prompt,
                             build_qwen_with_fallback, choose_engine, clean_output, max_tokens_for)


def fake_models(tmp_path, *names):
    for n in names:
        p = qwen.model_path(tmp_path, n)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
    return tmp_path


class FakeLlm:
    def __init__(self, text):
        self.text, self.calls = text, []

    def create_completion(self, prompt, **kw):
        self.calls.append((prompt, kw))
        return {"choices": [{"text": self.text}]}


def test_cli_engine_choices_default_auto():
    assert cli.parse_args([]).engine == "auto"
    for e in ("auto", "hymt", "opus"):
        assert cli.parse_args(["--engine", e]).engine == e
    for bad in ("gpt", "qwen", "qwen4b", "m2m"):  # #276: Qwen / m2m are no longer user choices
        with pytest.raises(SystemExit):
            cli.parse_args(["--engine", bad])


@pytest.mark.parametrize("free", [8000, QWEN8B_MIN_FREE_MIB, QWEN4B_MIN_FREE_MIB, 0, None])
def test_auto_never_picks_qwen_even_when_it_fits(tmp_path, free):
    """#276: Qwen GGUFs on disk and a big GPU, no Hy-MT2 -> still Hy-MT2 (the setup error comes later)."""
    m = fake_models(tmp_path, "qwen3-8b", "qwen3-4b")
    name, why = choose_engine("auto", "cuda", m, free_mib=lambda: free)
    assert name in ("hymt2", "hymt2-cpu") and why
    assert choose_engine("auto", "cpu", m, free_mib=lambda: 99999)[0] == "hymt2-cpu"


def test_explicit_engines_ignore_vram(tmp_path):
    boom = lambda: (_ for _ in ()).throw(AssertionError("vram read"))  # noqa: E731
    assert choose_engine("qwen", "cuda", tmp_path, free_mib=boom)[0] == "qwen3-8b"
    assert choose_engine("qwen4b", "cuda", tmp_path, free_mib=boom)[0] == "qwen3-4b"
    assert choose_engine("opus", "cuda", tmp_path, free_mib=boom)[0] == "opus"


def test_translator_prompt_think_stripping_and_opencc(tmp_path):
    m = fake_models(tmp_path, "qwen3-8b")
    llm = FakeLlm("<think>\nhmm\n</think>\n你好，这是软件。")
    t = QwenTranslator(m, "qwen3-8b", "tw", "cuda", loader=lambda p, gpu: llm)
    out = t.translate("Привет", "ru", "zh")
    assert out.pivot is None and "<think>" not in out.text and "軟體" in out.text  # s2twp
    prompt, kw = llm.calls[0]
    assert "<think>\n\n</think>" in prompt and "Russian" in prompt and "Traditional Chinese" in prompt
    assert kw["stop"] and kw["temperature"] == 0.0 and kw["max_tokens"] == max_tokens_for("Привет")
    assert t.translate("x", "ru", "ru").text == "x"
    cn = QwenTranslator(m, "qwen3-8b", "cn", "cuda", loader=lambda p, gpu: FakeLlm("數據"))
    assert cn.translate("a", "ru", "zh").text == "数据"
    assert "Simplified" in build_prompt(qwen.build_system_prompt("ru", "zh", "cn"), "x")


def test_clean_output_and_max_tokens():
    assert clean_output("a<think>unclosed") == "a"
    assert clean_output("好的" * 30) == "好的"  # repetition loop collapsed
    assert clean_output("正常  的\n句子") == "正常 的 句子"
    assert max_tokens_for("a") == 32 and max_tokens_for("x" * 100) == 216 and max_tokens_for("x" * 5000) == 400


def test_missing_gguf_names_setup(tmp_path):
    with pytest.raises(ModelNotFound, match="tlwu-setup --qwen"):
        QwenTranslator(tmp_path, "qwen3-8b")


def test_oom_falls_back_8b_to_4b_to_opus(tmp_path):
    m = fake_models(tmp_path, "qwen3-8b", "qwen3-4b")
    tried = []

    class Fac(QwenTranslator):
        def __init__(self, md, name, zh, dev):
            super().__init__(md, name, zh, dev, loader=self.load)
            self.n = name

        def load(self, path, gpu):
            tried.append(self.n)
            if self.n == "qwen3-8b" or FAIL4:
                raise ValueError("CUDA out of memory")
            return FakeLlm("x")

    FAIL4 = False
    warns = []
    tr, name = build_qwen_with_fallback(m, "qwen3-8b", "tw", "cuda", warn=warns.append, factory=Fac)
    assert name == "qwen3-4b" and tried == ["qwen3-8b", "qwen3-4b"] and len(warns) == 1
    FAIL4 = True
    tried.clear()
    assert build_qwen_with_fallback(m, "qwen3-8b", "tw", "cuda", warn=warns.append, factory=Fac) == (None, None)


def test_make_translator_auto_without_hymt_stops_with_setup_command(tmp_path):
    fake_models(tmp_path, "qwen3-8b")  # #276: a Qwen on disk is never used instead
    said = []
    with pytest.raises(SystemExit, match="tlwu-setup --hymt"):
        cli.make_translator("auto", tmp_path, "tw", "cpu", say=lambda m, **k: said.append(m))
    assert not any("engine = opus" in m or "qwen" in m for m in said)
    with pytest.raises(ModelNotFound):  # explicit (internal) Qwen without its GGUF: clear setup error
        cli.make_translator("qwen", tmp_path / "none", "tw", "cuda")


def test_setup_qwen_manifest_entries(monkeypatch, tmp_path):
    from tinglongwu_setup import downloader

    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(k, "1")
    got = []
    monkeypatch.setattr(downloader, "get_qwen", lambda man, mdir, name: got.append(name))
    assert downloader.main(["--qwen", "--models-dir", str(tmp_path)]) == 0
    assert got == ["qwen3-8b", "qwen3-4b"]
    assert downloader.QWEN_GGUF["qwen3-8b"][1] == qwen.QWEN_SPECS["qwen3-8b"].filename
    assert downloader.QWEN_GGUF["qwen3-4b"] == (qwen.QWEN_SPECS["qwen3-4b"].repo, qwen.QWEN_SPECS["qwen3-4b"].filename)
