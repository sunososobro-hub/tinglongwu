"""#283: engine registry (no Qwen), shared llama.cpp output helpers. No real models."""
import pytest

from tinglongwu import cli, engines
from tinglongwu.engines import choose_engine, clean_output, max_tokens_for


def test_cli_engine_choices_default_auto():
    assert cli.parse_args([]).engine == "auto"
    for e in ("auto", "hymt", "opus"):
        assert cli.parse_args(["--engine", e]).engine == e
    for bad in ("gpt", "qwen", "qwen4b", "m2m"):  # #276/#283: Qwen / m2m are gone
        with pytest.raises(SystemExit):
            cli.parse_args(["--engine", bad])


@pytest.mark.parametrize("free", [8000, 5800, 3500, 0, None])
def test_auto_is_always_hymt(tmp_path, free):
    name, why = choose_engine("auto", "cuda", tmp_path, free_mib=lambda: free)
    assert name in ("hymt2", "hymt2-cpu") and why
    assert choose_engine("auto", "cpu", tmp_path, free_mib=lambda: 99999)[0] == "hymt2-cpu"


def test_explicit_engines_ignore_vram(tmp_path):
    boom = lambda: (_ for _ in ()).throw(AssertionError("vram read"))  # noqa: E731
    assert choose_engine("hymt", "cuda", tmp_path, free_mib=boom)[0] == "hymt2"
    assert choose_engine("opus", "cuda", tmp_path, free_mib=boom)[0] == "opus"


def test_registry_entry_is_all_an_engine_needs_to_be_chosen(monkeypatch, tmp_path):
    """One dict entry per engine: a new entry is returned when requested (#279 Hy-MT2 7B adds one line)."""
    monkeypatch.setitem(engines.ENGINE_REGISTRY, "fake", "fake-engine-1")
    boom = lambda: (_ for _ in ()).throw(AssertionError("vram read"))  # noqa: E731
    assert choose_engine("fake", "cuda", tmp_path, free_mib=boom) == ("fake-engine-1", "requested")
    assert set(engines.ENGINES) == {"auto", "hymt", "opus"}  # the module-level CLI choices are unchanged


def test_clean_output_and_max_tokens():
    assert clean_output("a<think>unclosed") == "a"
    assert clean_output("<think>\nhmm\n</think>\n你好") == "你好"
    assert clean_output("好的" * 30) == "好的"  # repetition loop collapsed
    assert clean_output("正常  的\n句子") == "正常 的 句子"
    assert max_tokens_for("a") == 32 and max_tokens_for("x" * 100) == 216 and max_tokens_for("x" * 5000) == 400


def test_make_translator_auto_without_hymt_stops_with_setup_command(tmp_path):
    said = []
    with pytest.raises(SystemExit, match="tlwu-setup --hymt"):
        cli.make_translator("auto", tmp_path, "tw", "cpu", say=lambda m, **k: said.append(m))
    assert not any("engine = opus" in m for m in said)


def test_qwen_module_is_gone():
    with pytest.raises(ImportError):
        import tinglongwu.qwen  # noqa: F401
