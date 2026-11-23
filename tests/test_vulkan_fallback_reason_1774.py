"""#1774: every Vulkan -> CPU fallback prints exactly one line with the reason and a copy-paste fix."""
from types import SimpleNamespace

import pytest

from tinglongwu import asr_vulkan, mt_backend, vulkan_hint
from tinglongwu.vulkan_hint import fallback_line


def _one(lines, *needles):
    assert len(lines) == 1, lines
    line = lines[0]
    assert "\n" not in line
    for n in needles:
        assert n in line, line
    return line


def test_vulkan_fallback_reason_no_pack_linux():
    line = fallback_line("no_pack", platform="linux")
    assert "tlwu-setup --vulkan-pack" in line and "tlwu.exe" not in line and "using CPU" in line


def test_vulkan_fallback_reason_windows_keeps_exe():
    assert "tlwu.exe setup --vulkan-pack" in fallback_line("no_pack", platform="win32")


def test_vulkan_fallback_reason_pack_version(tmp_path):
    (tmp_path / "libllama.so").write_text("x")
    (tmp_path / "version.txt").write_text("0.0.1")
    got = []
    assert mt_backend.vulkan_pack_dir(tmp_path, warn=got.append) is None
    _one(got, "version 0.0.1", "tlwu-setup --vulkan-pack" if mt_backend.sys.platform != "win32" else "setup --vulkan-pack")


def test_vulkan_fallback_reason_init_failure_mt():
    got = []

    def loader(path, backend):
        if backend == "vulkan":
            raise RuntimeError("no ICD\nfound")
        return "cpu-model"

    model, used = mt_backend.load_with_fallback("m", "vulkan", loader, warn=got.append)
    assert (model, used) == ("cpu-model", "cpu")
    _one(got, "Vulkan init failed", "no ICD found", "Fix:", "--vulkan-pack")


def test_vulkan_fallback_reason_init_failure_asr():
    got = []

    def boom():
        raise OSError("device lost")

    rec, backend = asr_vulkan.select_recognizer(
        "m", cuda=False, vulkan=True, env={}, warn=got.append, cpu_factory=lambda d: "cpu", vulkan_factory=boom)
    assert (rec, backend) == ("cpu", "cpu")
    _one(got, "device lost", "for ASR", "Fix:")


def test_vulkan_fallback_reason_missing_model():
    line = fallback_line("missing_model", use="ASR", platform="linux", size="small")
    assert "ggml-small.bin" in line and "tlwu-setup --whisper-cpp small" in line and "tlwu.exe" not in line


def test_vulkan_fallback_reason_vulkan_not_found_linux():
    line = fallback_line("no_loader", platform="linux")
    assert "libvulkan.so.1" in line and "apt install" in line and "tlwu.exe" not in line


def test_vulkan_fallback_reason_forced_but_unavailable():
    env = {"TLWU_DEVICE": "vulkan"}
    assert mt_backend.forced_vulkan_unavailable(None, env=env) == "no_pack" or mt_backend.APP_ROOT.joinpath(
        mt_backend.VULKAN_PACK_NAME).is_dir()
    assert mt_backend.forced_vulkan_unavailable("pack", env=env, present=lambda: False) == "no_loader"
    assert mt_backend.forced_vulkan_unavailable("pack", env=env, present=lambda: True) == ""
    assert mt_backend.forced_vulkan_unavailable(None, env={}) == ""  # auto mode stays quiet


def test_vulkan_fallback_reason_asr_missing_model_and_no_pack():
    for exc, needle in ((FileNotFoundError("x"), "ggml-small.bin"), (ImportError("no Vulkan pack installed"), "no Vulkan pack installed")):
        got = []

        def boom(exc=exc):
            raise exc

        asr_vulkan.select_recognizer("m", "small", cuda=False, vulkan=True, env={}, warn=got.append,
                                     cpu_factory=lambda d: "cpu", vulkan_factory=boom)
        _one(got, needle, "Fix:", "for ASR")


def test_vulkan_fallback_reason_live_recognizer_forced_without_loader():
    from tinglongwu import cli

    got = []
    a = SimpleNamespace(compute="auto", model="small", lang="en")
    cli.live_recognizer(a, "models", "cpu", env={"TLWU_DEVICE": "vulkan"}, say=got.append,
                        vulkan=lambda: False, ready=lambda m, s: False,
                        select=lambda *x, **k: ("cpu-rec", "cpu"))
    assert len([g for g in got if "Fix:" in g]) == 1


def test_vulkan_fallback_reason_unknown_kind():
    with pytest.raises(ValueError):
        vulkan_hint.fallback_line("nope")
