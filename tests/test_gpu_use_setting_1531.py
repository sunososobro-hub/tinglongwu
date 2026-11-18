"""#1531: "Use GPU: Auto / Off" under Adjust > Advanced is the only CPU/GPU control."""
from tinglongwu.gui import core


def test_default_is_auto_and_passes_no_device():
    s = dict(core.DEFAULTS)
    assert s["gpu_use"] == "auto"
    assert "--device" not in core.settings_to_argv(s)


def test_off_forces_cpu():
    argv = core.settings_to_argv({**core.DEFAULTS, "gpu_use": "off"})
    assert argv[argv.index("--device") + 1] == "cpu"


def test_off_needs_restart_and_roundtrips(tmp_path):
    assert "gpu_use" in core.START_TIME_SETTINGS
    p = tmp_path / "s.json"
    core.save_settings({**core.DEFAULTS, "gpu_use": "off"}, p)
    assert core.load_settings(p)["gpu_use"] == "off"


def test_labels_exist_in_every_ui_language():
    from tinglongwu.i18n import MESSAGES

    for lang in ("en", "ru", "zh-TW", "ja"):
        assert MESSAGES[lang].get("gui_gpu_use", "").strip()
        assert set(core._CHOICES["gpu_use"][lang]) == {"auto", "off"}
        assert all(v.strip() for v in core._CHOICES["gpu_use"][lang].values())
        assert core.choice_label("gpu_use", "off", lang) == core._CHOICES["gpu_use"][lang]["off"]
