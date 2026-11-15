"""#894: listen to room sound (lecture / in-person meeting) through a chosen microphone."""
from types import SimpleNamespace

import pytest

from tinglongwu.capture import select_backend
from tinglongwu.capture.mic import MicSource
from tinglongwu.cli import parse_args
from tinglongwu.gui.core import DEFAULTS, load_settings, mic_inputs, scene_runs, settings_to_argv, watch_settings

MICS = ["Microphone (USB Audio)", "CABLE Output (VB-Audio Virtual Cable)", "BlackHole 2ch", "tlwu_mic",
        "Monitor of Built-in Audio", "麥克風陣列 (Realtek)"]


def test_mic_list_has_real_microphones_only():
    assert mic_inputs(MICS) == ["Microphone (USB Audio)", "麥克風陣列 (Realtek)"]


def _video(**kw):
    return dict(DEFAULTS, scene="video", lang="en", target="zh-tw", **kw)


def test_video_scene_with_mic_source_records_the_chosen_microphone():
    mode, run, speak = scene_runs(_video(source="mic", mic_device="USB Audio"))
    assert speak is None and run["source"] == "mic" and run["voice"] is False
    a = parse_args(settings_to_argv(run))
    assert a.mic and a.device == "USB Audio"
    src = select_backend(a.file, a.mic, a.device)
    assert isinstance(src, MicSource)
    mics = [SimpleNamespace(name=n) for n in MICS]
    sc = SimpleNamespace(all_microphones=lambda include_loopback=False: mics,
                         default_microphone=lambda: mics[-1])
    assert src._pick_microphone(sc).name == "Microphone (USB Audio)"


def test_default_microphone_without_a_pick():
    a = parse_args(settings_to_argv(watch_settings(_video(source="mic"))))
    assert a.mic and a.device is None


def test_video_scene_default_stays_pc_sound():
    run = watch_settings(_video())
    assert run["source"] == "system"
    a = parse_args(settings_to_argv(run))
    assert not a.mic


def test_meeting_listen_leg_stays_pc_sound_even_with_mic_source():
    """The meeting scene's mic is the speak leg; its listen leg keeps hearing the PC (the call)."""
    mode, listen, speak = scene_runs(dict(DEFAULTS, scene="meeting", lang="en", target="zh-tw", source="mic",
                                          mic_device="USB Audio"))
    assert listen["source"] == "system" and not parse_args(settings_to_argv(listen)).mic
    assert speak["source"] == "mic"
    assert parse_args(settings_to_argv(speak)).device == "USB Audio"  # my words come from the picked microphone


def test_mic_device_is_remembered(tmp_path):
    p = tmp_path / "gui.json"
    p.write_text('{"source": "mic", "mic_device": "USB Audio"}', "utf-8")
    s = load_settings(p, gpu=lambda: False)
    assert s["source"] == "mic" and s["mic_device"] == "USB Audio"


def test_gui_mic_picker_saves_the_choice(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app, core

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_override", "en")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(app, "mic_inputs", lambda: core.mic_inputs(MICS))
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    a = app.App()
    try:
        values = a.slot_widgets[13][1][0].cget("values")
        assert list(values) == [a.mic_default, "Microphone (USB Audio)", "麥克風陣列 (Realtek)"]
        a.v_mic_dev.set("麥克風陣列 (Realtek)")
        a.collect()
        assert a.settings["mic_device"] == "麥克風陣列 (Realtek)"
        a.v_mic_dev.set(a.mic_default)
        a.collect()
        assert a.settings["mic_device"] == ""
    finally:
        a.stop_ev.set()
        a.root.destroy()
