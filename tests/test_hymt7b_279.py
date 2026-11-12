"""#279: Hy-MT2 7B is an opt-in engine (`tlwu-setup --hymt7b`, `--engine hymt7b`); auto never picks it.
No real models: fake llama loader and a fake HF fetch."""
import pytest

from tinglongwu import cli, hymt
from tinglongwu.engines import choose_engine
from tinglongwu.hymt import hymt_path
from tinglongwu_setup import cli as setup_cli
from tinglongwu_setup import downloader


class FakeLlm:
    def __init__(self, reply="你好"):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, **kw):
        self.calls.append(messages[0]["content"])
        return {"choices": [{"message": {"content": self.reply}}]}


def put(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_cli_accepts_hymt7b_and_it_maps_to_the_7b_engine(tmp_path):
    assert cli.parse_args(["--engine", "hymt7b"]).engine == "hymt7b"
    assert choose_engine("hymt7b", "cuda", tmp_path, free_mib=lambda: 9000) == ("hymt2-7b", "requested")


@pytest.mark.parametrize("dev,free", [("cuda", 24000), ("cuda", 8000), ("cuda", 1000), ("cuda", None), ("cpu", 0)])
def test_auto_never_picks_7b_even_when_installed_and_vram_is_plenty(tmp_path, dev, free):
    put(hymt_path(tmp_path, "7b"))
    put(hymt_path(tmp_path))
    assert choose_engine("auto", dev, tmp_path, free_mib=lambda: free)[0] in ("hymt2", "hymt2-cpu")


def test_7b_path_is_separate_from_1_8b(tmp_path):
    assert hymt_path(tmp_path) == tmp_path / "hymt2-1.8b" / "Hy-MT2-1.8B-Q4_K_M.gguf"
    assert hymt_path(tmp_path, "7b") == tmp_path / "hymt2-7b" / "Hy-MT2-7B-Q4_K_M.gguf"


def test_engine_hymt7b_without_the_model_stops_with_its_setup_flag(tmp_path):
    put(hymt_path(tmp_path))  # 1.8B installed is not enough, and is never used instead
    with pytest.raises(SystemExit, match=r"tlwu-setup --hymt7b"):
        cli.make_translator("hymt7b", tmp_path, "tw", "cuda", say=lambda *a, **k: None)


def test_engine_hymt7b_loads_the_7b_gguf_and_translates(tmp_path, monkeypatch):
    p7 = put(hymt_path(tmp_path, "7b"))
    loaded, llm = [], FakeLlm("你好")
    monkeypatch.setattr(hymt, "_load_llama", lambda path, gpu: loaded.append((path, gpu)) or llm)
    said = []
    tr = cli.make_translator("hymt7b", tmp_path, "tw", "cuda", say=lambda m, **k: said.append(m))
    assert loaded == [(p7, True)]
    assert tr.translate("Hello", "en", "zh").text == "你好"
    assert any("engine = hymt2-7b on cuda" in m for m in said)


def test_engine_hymt7b_on_cpu_warns(tmp_path, monkeypatch):
    put(hymt_path(tmp_path, "7b"))
    monkeypatch.setattr(hymt, "_load_llama", lambda path, gpu: FakeLlm())
    said = []
    cli.make_translator("hymt7b", tmp_path, "tw", "cpu", say=lambda m, **k: said.append(m))
    assert any("WARNING: Hy-MT2 7B on the CPU" in m for m in said)


def test_setup_refuses_7b_when_license_is_not_apache_before_downloading_the_gguf(tmp_path, monkeypatch):
    gguf = []
    monkeypatch.setattr(downloader, "get_gguf", lambda *a, **k: gguf.append(a))

    def fetch():  # upstream ships a non-Apache license
        put(tmp_path / "hymt2-7b" / "LICENSE.txt").write_text("TENCENT HY COMMUNITY LICENSE AGREEMENT", "utf-8")

    with pytest.raises(SystemExit, match="not Apache-2.0"):
        downloader.get_hymt7b({}, tmp_path, fetch_license=fetch)
    assert gguf == []


def test_setup_refuses_7b_when_license_file_is_missing(tmp_path, monkeypatch):
    gguf = []
    monkeypatch.setattr(downloader, "get_gguf", lambda *a, **k: gguf.append(a))
    with pytest.raises(SystemExit, match="missing"):
        downloader.get_hymt7b({}, tmp_path, fetch_license=lambda: None)
    assert gguf == []


def test_setup_downloads_7b_gguf_after_apache_license(tmp_path, monkeypatch):
    gguf = []
    monkeypatch.setattr(downloader, "get_gguf", lambda *a, **k: gguf.append((a[2:], k)))

    def fetch():
        put(tmp_path / "hymt2-7b" / "LICENSE.txt").write_text(
            "Hy-MT2 is licensed under the Apache License, Version 2.0.", "utf-8")

    downloader.get_hymt7b({}, tmp_path, fetch_license=fetch)
    assert gguf == [(("hymt2-7b", "tencent/Hy-MT2-7B-GGUF", "Hy-MT2-7B-Q4_K_M.gguf"), {"extras": ("LICENSE.txt",)})]


def test_tlwu_setup_hymt7b_flag_reaches_the_7b_download(tmp_path, monkeypatch):
    import os

    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):  # main() edits them: restore after
        monkeypatch.setenv(k, os.environ.get(k, "1"))
    called = []
    monkeypatch.setattr(downloader, "get_hymt7b", lambda man, mdir: called.append(mdir))
    monkeypatch.setattr(downloader, "get_hymt", lambda *a: pytest.fail("1.8B must not be downloaded"))
    assert setup_cli.main(["--hymt7b", "--models-dir", str(tmp_path)]) in (0, None)
    assert [str(m) for m in called] == [str(tmp_path)]
