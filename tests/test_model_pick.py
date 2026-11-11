from tinglongwu import models
from tinglongwu.gui import core


def _mk(tmp_path, *sizes):
    for s in sizes:
        (tmp_path / f"whisper-{s}").mkdir()


def test_uses_wanted_when_downloaded(tmp_path):
    _mk(tmp_path, "small", "large-v3-turbo")
    assert models.pick_whisper("small", tmp_path) == "small"


def test_falls_back_to_downloaded_model(tmp_path):
    _mk(tmp_path, "large-v3-turbo")
    assert models.pick_whisper("small", tmp_path) == "large-v3-turbo"
    assert models.whisper_installed(tmp_path) == ["large-v3-turbo"]


def test_nothing_downloaded_keeps_wanted_so_setup_hint_shows(tmp_path):
    assert models.pick_whisper("small", tmp_path) == "small"


def test_gui_argv_uses_downloaded_model(tmp_path, monkeypatch):
    _mk(tmp_path, "large-v3-turbo")
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path))
    argv = core.settings_to_argv({"model": "small", "quality": "manual"})
    assert argv[argv.index("--model") + 1] == "large-v3-turbo"


def test_dropdown_lists_downloaded_first_and_marked(tmp_path):
    _mk(tmp_path, "large-v3-turbo")
    labels = core.model_labels(tmp_path)
    assert labels[0] == "✓ large-v3-turbo"
    assert all("not downloaded" in l for l in labels[1:])
    assert [core.model_from_label(l) for l in labels][0] == "large-v3-turbo"
    assert sorted(core.model_from_label(l) for l in labels) == sorted(core.MODELS)
    assert core.label_for_model("small", tmp_path).startswith("small")
    assert core.model_from_label("small") == "small"


# --- #96 item 2: fallback order follows the hardware tier --------------------------------------------------

def test_cpu_tier_prefers_small_over_turbo(tmp_path):
    _mk(tmp_path, "large-v3-turbo", "small", "medium")
    assert models.pick_whisper("base", tmp_path, "cpu") == "small"
    assert models.pick_whisper("base", tmp_path, "gpu") == "large-v3-turbo"


def test_cpu_tier_avoids_medium_and_large(tmp_path):
    _mk(tmp_path, "medium", "large-v3", "tiny")
    assert models.pick_whisper("small", tmp_path, "cpu") == "tiny"
    assert models.pick_whisper("small", tmp_path, None) == "medium"  # unknown tier = GPU order (old behaviour)


def test_cli_auto_model_uses_tier_order(tmp_path):
    from tinglongwu.cli import auto_model
    from tinglongwu.hwprobe import HwInfo

    _mk(tmp_path, "large-v3-turbo", "base")
    cpu = HwInfo(gpu=None, vram_mib=None, ram_mib=16384, cores=8)
    # CPU tier wants small (missing): base is the next CPU choice, not the heavier turbo
    assert auto_model("balanced", "cpu", tmp_path, hw=cpu, say=lambda *a, **k: None) == "base"
    gpu = HwInfo(gpu="RTX", vram_mib=8192, ram_mib=16384, cores=8, driver="560.00")
    _mk(tmp_path, "small")
    assert auto_model("fast", "cuda", tmp_path, hw=gpu, say=lambda *a, **k: None) == "small"
    (tmp_path / "whisper-small").rmdir()
    assert auto_model("fast", "cuda", tmp_path, hw=gpu, say=lambda *a, **k: None) == "large-v3-turbo"


def test_gui_usable_model_uses_tier_and_skips_probe_when_present(tmp_path):
    _mk(tmp_path, "large-v3-turbo", "small")
    calls = []
    assert core.usable_model("small", tmp_path, tier=lambda: calls.append(1) or "gpu") == "small"
    assert calls == []  # chosen model on disk: no nvidia-smi probe
    assert core.usable_model("medium", tmp_path, tier=lambda: "cpu") == "small"
    assert core.usable_model("medium", tmp_path, tier=lambda: "gpu") == "large-v3-turbo"


def test_hw_tier_name_from_probe_and_probe_failure():
    from tinglongwu.hwprobe import HwInfo

    assert core.hw_tier_name(lambda: HwInfo(gpu=None, vram_mib=None, ram_mib=8192, cores=4)) == "cpu"

    def boom():
        raise OSError("no nvidia-smi")

    assert core.hw_tier_name(boom) is None


# --- #96 item 3: ask-then-download in a tlwu-setup child process ------------------------------------------

def test_download_cmd_source_and_bundled(tmp_path):
    import sys

    src = core.download_cmd("medium", tmp_path, bundled=False)
    assert src[:3] == [sys.executable, "-m", "tinglongwu_setup"]
    assert src[3:] == ["--whisper", "medium", "--models-dir", str(tmp_path)]
    exe = core.download_cmd("medium", tmp_path, bundled=True)
    assert exe[1:] == ["--run-setup", "--whisper", "medium", "--models-dir", str(tmp_path)]


def test_download_env_drops_offline_flags():
    env = core.download_env({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PATH": "/bin", "TLWU_MODELS": "m"})
    assert "HF_HUB_OFFLINE" not in env and "TRANSFORMERS_OFFLINE" not in env
    assert env["PATH"] == "/bin" and env["TLWU_MODELS"] == "m"


def test_run_download_reports_lines_and_result(tmp_path):
    import sys

    events = []
    # fake tlwu-setup child: prints progress (tqdm-style \r) and creates the model dir
    script = ("import sys,pathlib; print('fetch 10%\\rfetch 100%', flush=True); "
              "pathlib.Path(sys.argv[sys.argv.index('--models-dir')+1], 'whisper-'+sys.argv[sys.argv.index('--whisper')+1]).mkdir()")

    def popen(cmd, **kw):
        import subprocess
        assert "HF_HUB_OFFLINE" not in kw["env"]
        return subprocess.Popen([sys.executable, "-c", script, *cmd[cmd.index("--whisper"):]], stdout=kw["stdout"],
                                stderr=kw["stderr"], env=kw["env"])

    assert core.run_download("medium", events.append, tmp_path, popen=popen, bundled=False) == 0
    assert ("dl_line", "fetch 100%") in events
    assert events[-1] == ("dl_done", "medium", 0)
    assert core.is_downloaded("medium", tmp_path)


def test_run_download_failure_to_start(tmp_path):
    events = []

    def popen(*a, **k):
        raise FileNotFoundError("tlwu-setup")

    assert core.run_download("tiny", events.append, tmp_path, popen=popen) == -1
    assert events[-1] == ("dl_done", "tiny", -1)


def test_model_combo_wide_enough_for_not_downloaded_label(tmp_path):
    labels = core.model_labels(tmp_path)
    longest = max(labels, key=core.text_width)
    assert core.combo_width(labels) > 18 and core.combo_width(labels) >= core.text_width(longest)
    assert core.text_width("需下載") == 6
