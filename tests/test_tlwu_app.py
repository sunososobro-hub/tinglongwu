"""tlwu.exe launcher: GPU tiers, setup planning, entry routing, CUDA fallback notice (no display, no network)."""
import ast
import sys
from pathlib import Path

import pytest

from tlwu_app import main as app_main
from tlwu_app import setup_gui, tiers

ROOT = Path(__file__).resolve().parent.parent


def gpu(total):
    return tiers.Gpu("Test GPU", total, total - 500)


@pytest.mark.parametrize("total,name,whisper,mt", [
    (24576, "gpu", "large-v3-turbo", ("--hymt",)),
    (8192, "gpu", "large-v3-turbo", ("--hymt",)),  # #106: no Qwen by VRAM any more, 8 GB gets Hy-MT2 too
    (6144, "gpu", "large-v3-turbo", ("--hymt",)),
    (4096, "gpu", "large-v3-turbo", ("--hymt",)),
    (4095, "gpu-min", "small", ("--hymt",)),  # #301: Hy-MT2 runs on the CPU below 4 GB VRAM
    (2048, "gpu-min", "small", ("--hymt",)),
])
def test_tier_by_total_vram_matches_install_ps1(total, name, whisper, mt):
    t = tiers.choose_tier(gpu(total))
    assert (t.name, t.whisper, t.mt) == (name, whisper, mt)


def test_gpu_tier_threshold_is_the_hwprobe_one():
    from tinglongwu import hwprobe
    assert tiers.GPU_MIN_TOTAL_MB == hwprobe.GPU_MIN_VRAM_MIB
    hw = hwprobe.HwInfo(gpu="Test GPU", vram_mib=tiers.GPU_MIN_TOTAL_MB, ram_mib=16384, cores=8, driver="560.0")
    assert hwprobe.recommend(hw).mt == "hymt2" and tiers.choose_tier(gpu(hw.vram_mib)).mt == ("--hymt",)


def test_no_gpu_or_forced_cpu_is_cpu_tier():
    for t in (tiers.choose_tier(None), tiers.choose_tier(gpu(24000), force_cpu=True)):
        assert (t.name, t.whisper, t.mt) == ("cpu", "small", ("--hymt",))
    assert tiers.T_CPU.downloader_args() == ["--whisper", "small", "--packs", "zh,en", "--allow-unpinned", "--hymt"]
    assert tiers.T_MIN.downloader_args() == ["--whisper", "small", "--packs", "zh,en", "--allow-unpinned", "--hymt"]
    assert tiers.T_GPU.downloader_args() == ["--whisper", "large-v3-turbo", "--packs", "zh,en", "--allow-unpinned", "--hymt"]
    assert "GB" in tiers.T_GPU.size_text and "GB" in tiers.T_CPU.size_text  # small + OPUS + Hy-MT2 ~1.8 GB
    assert tiers.T_GPU.total_bytes < 3.5 * 1024 * tiers.MB  # turbo + OPUS + Hy-MT2, not the old ~10 GB Qwen tier


def test_parse_smi_and_detect_gpu(monkeypatch):
    assert tiers.parse_smi("NVIDIA GeForce RTX 3050, 8192, 7478\n") == tiers.Gpu("NVIDIA GeForce RTX 3050", 8192, 7478)
    assert tiers.parse_smi("") is None and tiers.parse_smi("garbage") is None
    monkeypatch.setattr(tiers, "_nvidia_smi", lambda: None)
    assert tiers.detect_gpu() is None  # no nvidia-smi -> CPU tier
    monkeypatch.setattr(tiers, "_nvidia_smi", lambda: "nvidia-smi")

    class R:
        stdout = "RTX, 6144, 6000\n"

    assert tiers.detect_gpu(run=lambda *a, **k: R()).total_mb == 6144

    def boom(*a, **k):
        raise OSError("driver")

    assert tiers.detect_gpu(run=boom) is None


def _fake_models(d, whisper=("small",), opus=True, hymt=True):
    for w in whisper:
        (d / f"whisper-{w}").mkdir(parents=True)
        (d / f"whisper-{w}" / "model.bin").write_bytes(b"x")
    if opus:
        (d / "opus" / "en-zh" / "model").mkdir(parents=True)
        (d / "opus" / "en-zh" / "model" / "model.bin").write_bytes(b"x")
    if hymt:
        (d / "hymt2-1.8b").mkdir(parents=True, exist_ok=True)
        (d / "hymt2-1.8b" / "Hy-MT2-1.8B-Q4_K_M.gguf").write_bytes(b"x")


def test_models_ready_and_best_installed(tmp_path):
    assert not tiers.models_ready(tmp_path / "none")
    _fake_models(tmp_path, ("small",), opus=False)
    assert not tiers.models_ready(tmp_path)  # whisper without the translation pair
    (tmp_path / "opus" / "en-zh" / "model").mkdir(parents=True)
    (tmp_path / "opus" / "en-zh" / "model" / "model.bin").write_bytes(b"x")
    assert tiers.models_ready(tmp_path)
    assert tiers.best_installed_model(tmp_path, "large-v3-turbo") == "small"  # wanted one is missing
    _fake_models(tmp_path.parent / "m2", ("small", "large-v3-turbo"))
    assert tiers.best_installed_model(tmp_path.parent / "m2", "small") == "small"
    assert tiers.best_installed_model(tmp_path.parent / "m2", None) == "large-v3-turbo"


def test_models_ready_needs_the_hymt_gguf(tmp_path):
    """#301: an older install (Whisper + OPUS, no Hy-MT2) must reopen the first-run setup, tlwu stops without Hy."""
    _fake_models(tmp_path, ("small",), hymt=False)
    assert not tiers.models_ready(tmp_path)
    _fake_models(tmp_path / "x", ("small",))
    assert tiers.models_ready(tmp_path / "x")


def test_progress_fraction_capped():
    t = tiers.T_CPU
    assert tiers.progress_fraction(0, t) == 0
    assert tiers.progress_fraction(t.total_bytes * 5, t) == 0.99
    assert 0.4 < tiers.progress_fraction(t.total_bytes // 2, t) < 0.6


def test_dir_size(tmp_path):
    (tmp_path / "a").write_bytes(b"1234")
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "b").write_bytes(b"12")
    assert tiers.dir_size(tmp_path) == 6


def test_default_models_dir_is_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tiers.default_models_dir() == tmp_path / "tinglongwu" / "models"


def test_prepare_env_bundled_sets_models_and_cpu(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for k in ("TLWU_MODELS", "TLWU_BUNDLED", "CUDA_VISIBLE_DEVICES"):
        monkeypatch.delenv(k, raising=False)
    import os

    assert app_main.prepare_env(True, True) == tmp_path / "tinglongwu" / "models"
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "-1" and os.environ["TLWU_BUNDLED"] == "1"
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path / "mine"))  # explicit env wins
    assert app_main.prepare_env(True, False) == tmp_path / "mine"
    monkeypatch.delenv("TLWU_BUNDLED")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES")


def test_split_cpu():
    assert app_main.split_cpu(["--cpu", "--file", "a"]) == (["--file", "a"], True)
    assert app_main.split_cpu([]) == ([], False)


def test_routing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(app_main, "run_gui", lambda *a: calls.append(("gui", a[2], a[3])) or 0)
    monkeypatch.setattr(app_main, "run_setup_worker", lambda a: calls.append(("setup", a)) or 0)
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path))
    monkeypatch.delenv("TLWU_BUNDLED", raising=False)
    assert app_main.main([]) == 0 and calls[-1] == ("gui", False, False)
    app_main.main(["--cpu"])
    assert calls[-1] == ("gui", True, False)
    app_main.main(["--setup"])
    assert calls[-1] == ("gui", False, True)
    app_main.main(["--run-setup", "--verify"])
    assert calls[-1] == ("setup", ["--verify"])
    app_main.main(["setup", "--whisper", "small"])
    assert calls[-1] == ("setup", ["--whisper", "small", "--models-dir", str(tmp_path)])
    import os

    os.environ.pop("CUDA_VISIBLE_DEVICES", None)


def test_cli_flags_pass_through(monkeypatch, tmp_path):
    seen = []
    import tinglongwu.cli as cli

    monkeypatch.setattr(cli, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path))
    app_main.main(["--file", "x.wav", "--to", "zh"])
    assert seen[-1] == ["--file", "x.wav", "--to", "zh"]
    app_main.main(["--cpu", "--file", "x.wav"])
    assert seen[-1] == ["--file", "x.wav", "--device", "cpu"]
    import os

    os.environ.pop("CUDA_VISIBLE_DEVICES", None)


def test_worker_cmd():
    d = Path("M")
    assert setup_gui.worker_cmd(False, d, ["--whisper", "small"]) == [
        sys.executable, "-m", "tlwu_app", "--run-setup", "--whisper", "small", "--models-dir", "M"]
    c = setup_gui.worker_cmd(True, d, ["--qwen"])
    assert c[1:] == ["--run-setup", "--qwen", "--models-dir", "M"]


def test_selftest_cmd_after_download_runs_tlwu_selftest_offline():  # #99
    d = Path("M")
    gpu, cpu = tiers.choose_tier(tiers.Gpu("RTX", 8192, 8000)), tiers.choose_tier(None)
    assert setup_gui.selftest_cmd(False, d, cpu) == [
        sys.executable, "-m", "tlwu_app", "--selftest", "--model", cpu.whisper, "--models-dir", "M", "--device", "cpu"]
    c = setup_gui.selftest_cmd(True, d, gpu)
    assert c[1:] == ["--selftest", "--model", gpu.whisper, "--models-dir", "M"]


def test_selftest_verdict_warns_only_on_a_real_failure():  # #99
    assert setup_gui.selftest_verdict(0, "asr: OK") == ("下載完成，自我測試通過！", None)
    old_tlwu = "usage: tlwu [-h]\ntlwu: error: unrecognized arguments: --selftest"
    assert setup_gui.selftest_verdict(2, old_tlwu) == ("下載完成！", None)
    status, warn = setup_gui.selftest_verdict(1, "mt: FAIL qwen: out of memory")
    assert "自我測試失敗" in status and "install.log" in warn and "out of memory" in warn


def test_last_line_handles_tqdm_carriage_returns():
    assert setup_gui.last_line("a\r 10%\r 50%|#### |\n") == "50%|#### |"
    assert setup_gui.last_line("\n\r") == ""


def test_fix_gui_settings_picks_installed_model(monkeypatch, tmp_path):
    from tinglongwu.gui import core

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    mdir = tmp_path / "models"
    _fake_models(mdir, ("large-v3-turbo",))
    app_main.fix_gui_settings(mdir, cpu_tier=True)  # first run, CPU tier
    s = core.load_settings()
    assert s["model"] == "large-v3-turbo" and s["live_draft"] is False
    s["live_draft"] = True
    core.save_settings(s)
    app_main.fix_gui_settings(mdir, cpu_tier=True)  # not first run any more: user choice kept
    assert core.load_settings()["live_draft"] is True


def test_gui_source_setting_maps_to_mic():
    from tinglongwu.gui import core

    assert "--mic" not in core.settings_to_argv(core.load_settings(None))
    s = dict(core.DEFAULTS, source="mic")
    assert "--mic" in core.settings_to_argv(s)


def test_run_session_notice_when_gpu_unusable(monkeypatch, tmp_path):
    """CUDA libs missing -> CPU fallback with a visible notice (the GUI shows it)."""
    from tinglongwu import cli, device

    notes = []

    def fake_resolve(choice="auto", **kw):
        kw["warn"]("tlwu: CUDA device found but cublas64_12.dll missing; falling back to CPU.")
        return "cpu"

    monkeypatch.setattr(device, "resolve_device", fake_resolve)

    class Stop(Exception):
        pass

    import tinglongwu.asr as asr

    def boom(*a, **k):
        raise Stop

    monkeypatch.setattr(asr, "FasterWhisperRecognizer", boom)
    a = cli.parse_args(["--models-dir", str(tmp_path)])
    with pytest.raises(Stop):
        cli.run_session(a, notice=notes.append)
    assert notes and "CPU" in notes[0] and "cublas64_12.dll" in notes[0]


def test_bundled_cuda_dir_registered(monkeypatch, tmp_path):
    from tinglongwu import device

    fake = tmp_path / "pkg" / "tinglongwu"
    fake.mkdir(parents=True)
    (tmp_path / "pkg" / "cuda_libs").mkdir()
    monkeypatch.setattr(device, "__file__", str(fake / "device.py"))
    assert device._bundled_dirs() == [tmp_path / "pkg" / "cuda_libs"]


def test_launcher_keeps_network_and_runtime_apart():
    """tlwu_app may not import tinglongwu / tinglongwu_setup at module import time (setup stays in a child process)."""
    for f in (ROOT / "tlwu_app").glob("*.py"):
        tree = ast.parse(f.read_text("utf-8"))
        for node in tree.body:  # top level only
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            assert not any(n.split(".")[0] in ("tinglongwu", "tinglongwu_setup") for n in names), (f, names)
