"""#834: GUI send-mode setting (default confirm, saved in the profile, notice for countdown/auto)."""
from tinglongwu import i18n
from tinglongwu.gui.core import load_settings, quick_settings, save_settings, send_mode_notice_key


def test_fresh_profile_defaults_to_confirm(tmp_path):
    assert load_settings(tmp_path / "gui.json", gpu=lambda: False)["send_mode"] == "confirm"


def test_countdown_survives_save_and_reload(tmp_path):
    p = tmp_path / "gui.json"
    s = load_settings(p, gpu=lambda: False)
    s["send_mode"] = "countdown"
    assert save_settings(s, p)
    assert load_settings(p, gpu=lambda: False)["send_mode"] == "countdown"


def test_invalid_saved_mode_falls_back_to_confirm(tmp_path):
    p = tmp_path / "gui.json"
    p.write_text('{"send_mode": "yolo"}', "utf-8")
    assert load_settings(p, gpu=lambda: False)["send_mode"] == "confirm"


def test_notice_shown_for_countdown_and_auto_hidden_for_confirm(monkeypatch):
    monkeypatch.setattr(i18n, "_override", "en")
    assert send_mode_notice_key("confirm") is None
    for mode in ("countdown", "auto"):
        assert i18n.t(send_mode_notice_key(mode)) == (
            "Translation quality is not stable yet; sending directly is not recommended - "
            "check the translation first, then send.")
    monkeypatch.setattr(i18n, "_override", "zh-TW")
    assert i18n.t(send_mode_notice_key("auto")) == "翻譯品質還不夠穩，不建議直接送出；建議先確認翻譯對不對再送出"


def test_mode_reaches_the_speak_run(tmp_path):
    s = load_settings(tmp_path / "none.json", gpu=lambda: False)
    s["send_mode"] = "auto"
    assert quick_settings(s, "speak")["send_mode"] == "auto"
