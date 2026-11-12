"""#81: Fast / Balanced / Quality presets wired to the hardware probe; manual override; choice persisted."""
import json

import pytest

from tinglongwu import cli
from tinglongwu.gui import core
from tinglongwu.hwprobe import HwInfo, preset, recommend

GPU8 = HwInfo("GeForce RTX 3050", 8192, 16384, 12, driver="560.94")
GPU4 = HwInfo("GeForce GTX 1650", 4096, 8192, 8, driver="560.94")
CPU = HwInfo(None, None, 8192, 8)


def have(tmp_path, *sizes):
    for s in sizes:
        (tmp_path / f"whisper-{s}").mkdir()
    return tmp_path


def test_presets_per_tier():
    assert [preset(q, GPU8).asr for q in ("fast", "balanced", "quality")] == ["small", "large-v3-turbo", "large-v3"]
    assert [preset(q, CPU).asr for q in ("fast", "balanced", "quality")] == ["base", "small", "large-v3-turbo"]
    assert preset("quality", GPU4).asr == "large-v3-turbo"  # large-v3 + Hy-MT2 does not fit in 4 GB
    for hw in (GPU8, GPU4, CPU):
        assert preset("auto", hw).asr == preset("balanced", hw).asr == recommend(hw).asr
        assert {preset(q, hw).mt for q in ("fast", "balanced", "quality")} == {recommend(hw).mt}  # owner: CPU = OPUS
    with pytest.raises(ValueError):
        preset("ultra", CPU)


def test_auto_model_follows_hardware_and_falls_back_to_downloaded(tmp_path):
    said = []
    say = lambda m, **k: said.append(m)  # noqa: E731
    m = have(tmp_path, "small", "large-v3-turbo")
    assert cli.auto_model("auto", "cuda", m, hw=GPU8, say=say) == "large-v3-turbo"
    assert cli.auto_model("auto", "cpu", m, hw=CPU, say=say) == "small"
    assert cli.auto_model("auto", "cpu", m, hw=GPU8, say=say) == "small"  # GPU present but not usable -> CPU tier
    assert cli.auto_model("fast", "cuda", m, hw=GPU8, say=say) == "small"
    said.clear()
    assert cli.auto_model("quality", "cuda", m, hw=GPU8, say=say) == "large-v3-turbo"  # large-v3 not downloaded
    assert "large-v3 is not downloaded" in said[-1] and "quality" in said[-1]


def test_auto_model_survives_a_broken_probe(tmp_path, monkeypatch):
    def boom():
        raise OSError("nvidia-smi exploded")

    monkeypatch.setattr("tinglongwu.hwprobe.probe", boom)
    said = []
    assert cli.auto_model("auto", "cuda", have(tmp_path, "small"), say=lambda m, **k: said.append(m)) == "small"
    assert any("probe failed" in s for s in said)


def test_cli_quality_flag_and_explicit_model():
    assert cli.parse_args([]).quality == "auto" and cli.parse_args([]).model is None
    a = cli.parse_args(["--quality", "fast", "--model", "medium"])
    assert (a.quality, a.model) == ("fast", "medium")  # run_session only auto-picks when --model is absent
    with pytest.raises(SystemExit):
        cli.parse_args(["--quality", "ultra"])


def test_gui_default_path_has_no_model_or_engine_and_manual_overrides(tmp_path, monkeypatch):
    argv = core.settings_to_argv(dict(core.DEFAULTS))
    assert "--model" not in argv and "--engine" not in argv
    assert cli.parse_args(argv).quality == "auto" and cli.parse_args(argv).model is None
    assert cli.parse_args(core.settings_to_argv(dict(core.DEFAULTS, quality="quality"))).quality == "quality"
    monkeypatch.setenv("TLWU_MODELS", str(have(tmp_path, "medium")))
    a = cli.parse_args(core.settings_to_argv(dict(core.DEFAULTS, quality="manual", model="medium", engine="opus")))
    assert (a.model, a.engine) == ("medium", "opus")
    assert cli.parse_args(core.settings_to_argv(dict(core.DEFAULTS, quality="bogus"))).quality == "auto"


def test_quality_choice_persists_and_old_hand_picked_settings_become_manual(tmp_path):
    p = tmp_path / "gui.json"
    assert core.load_settings(p)["quality"] == "auto"  # first run: recommended
    assert core.save_settings(dict(core.DEFAULTS, quality="fast"), p)
    assert core.load_settings(p)["quality"] == "fast"
    p.write_text(json.dumps({"model": "medium", "engine": "qwen"}), "utf-8")  # saved before #81
    assert core.load_settings(p)["quality"] == "manual"
    p.write_text(json.dumps({"model": "small", "engine": "auto", "lang": "ru"}), "utf-8")
    assert core.load_settings(p)["quality"] == "auto"
    p.write_text(json.dumps({"quality": "nonsense"}), "utf-8")
    assert core.load_settings(p)["quality"] == "auto"


@pytest.mark.parametrize("old", ["qwen", "qwen4b", "m2m"])
def test_saved_removed_engine_becomes_auto_and_parses(tmp_path, old):
    """#276: a GUI setting saved with an engine that is no longer a CLI choice must not crash the start."""
    p = tmp_path / "gui.json"
    p.write_text(json.dumps({"model": "small", "engine": old, "quality": "manual"}), "utf-8")
    s = core.load_settings(p)
    assert s["engine"] == "auto"
    assert cli.parse_args(core.settings_to_argv(s)).engine == "auto"
