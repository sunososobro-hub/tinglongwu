"""#1761: auto speech-model pick also sees whisper.cpp ggml sizes when Vulkan ASR is usable."""
from tinglongwu import asr_vulkan, cli


def _mk(tmp_path, folders=(), ggml=()):
    for s in folders:
        (tmp_path / f"whisper-{s}").mkdir()
    if ggml:
        (tmp_path / "whisper-cpp").mkdir()
        for s in ggml:
            (tmp_path / "whisper-cpp" / f"ggml-{s}.bin").write_bytes(b"x")
    return tmp_path


def _ready(monkeypatch, tmp_path):
    monkeypatch.setattr(asr_vulkan, "vulkan_ready",
                        lambda m, size, pack_dir=None: (tmp_path / "whisper-cpp" / f"ggml-{size}.bin").is_file())


def test_ggml_base_with_fw_small_picks_base_on_vulkan(tmp_path, monkeypatch):
    m = _mk(tmp_path, folders=["small"], ggml=["base"])
    _ready(monkeypatch, m)
    said = []
    got = cli.auto_model("auto", "cpu", m, say=lambda *a, **k: said.append(a[0]), vulkan=lambda: True)
    assert got == "base"
    assert any("base" in x and "Vulkan" in x for x in said)


def test_ggml_only_install_starts_on_vulkan(tmp_path, monkeypatch):
    m = _mk(tmp_path, ggml=["small"])
    _ready(monkeypatch, m)
    assert cli.auto_model("auto", "cpu", m, say=lambda *a, **k: None, vulkan=lambda: True) == "small"


def test_no_vulkan_or_cuda_keeps_faster_whisper_pick(tmp_path, monkeypatch):
    m = _mk(tmp_path, folders=["small"], ggml=["base"])
    _ready(monkeypatch, m)
    assert cli.auto_model("auto", "cpu", m, say=lambda *a, **k: None, vulkan=lambda: False) == "small"
    assert cli.auto_model("auto", "cuda", m, say=lambda *a, **k: None, vulkan=lambda: True) == "small"


def test_no_false_not_downloaded_note_when_fw_wanted_is_installed(tmp_path, monkeypatch):
    m = _mk(tmp_path, folders=["small"], ggml=["base"])
    _ready(monkeypatch, m)
    said = []
    cli.auto_model("auto", "cpu", m, say=lambda *a, **k: said.append(a[0]), vulkan=lambda: True)
    assert not any("is not downloaded" in x for x in said)
