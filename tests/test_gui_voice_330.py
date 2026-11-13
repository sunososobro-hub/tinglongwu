"""#330 part 2: GUI voice checkbox -> the same `tlwu --voice` the CLI runs (settings, argv, output list)."""
from __future__ import annotations

import json

from tinglongwu import cli
from tinglongwu.gui import core


def test_voice_is_off_in_default_gui_settings_and_adds_no_flag(tmp_path):
    s = core.load_settings(tmp_path / "none.json", gpu=lambda: True)
    assert s["voice"] is False and s["voice_device"] == ""
    a = cli.parse_args(core.settings_to_argv(s))
    assert a.voice is False and a.voice_device is None


def test_voice_checkbox_runs_the_cli_voice_on_the_chosen_output(tmp_path):
    s = dict(core.DEFAULTS, voice=True, voice_device="Headphones (USB Audio)")
    a = cli.parse_args(core.settings_to_argv(s))
    assert a.voice is True and a.voice_device == "Headphones (USB Audio)" and a.voice_speaker == 0
    a = cli.parse_args(core.settings_to_argv(dict(s, voice_device="")))  # "(default output)"
    assert a.voice is True and a.voice_device is None


def test_voice_choice_survives_a_restart(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"voice": True, "voice_device": "Kopfhörer"}), "utf-8")
    s = core.load_settings(p, gpu=lambda: True)
    assert s["voice"] is True and s["voice_device"] == "Kopfhörer"
    p.write_text(json.dumps({"voice": "yes", "voice_device": 3}), "utf-8")  # wrong types fall back to defaults
    s = core.load_settings(p, gpu=lambda: True)
    assert s["voice"] is False and s["voice_device"] == ""


def test_voice_outputs_leave_out_devices_the_listen_side_can_record():
    names = ["Speakers (Realtek(R) Audio)", "CABLE Input (VB-Audio Virtual Cable)", "BlackHole 2ch", "tlwu_sink",
             "Monitor of Built-in Audio", "Headphones (USB Audio)", "Multi-Output Device", ""]
    assert core.voice_outputs(names) == ["Speakers (Realtek(R) Audio)", "Headphones (USB Audio)"]


def test_report_says_whether_the_voice_was_on():
    assert core.report_info(dict(core.DEFAULTS, voice=True))["Voice"] == "on"
    assert core.report_info(dict(core.DEFAULTS))["Voice"] == "off"


def test_unplugged_saved_voice_device_falls_back_to_default_with_one_notice():  # #448
    s = dict(core.DEFAULTS, voice=True, voice_device="Headphones (USB Audio)")
    notes = []
    argv = core.settings_to_argv(s, outputs=["Speakers (Realtek(R) Audio)"], notice=notes.append)
    a = cli.parse_args(argv)
    assert "--voice-device" not in argv and a.voice is True and a.voice_device is None
    assert len(notes) == 1 and "Headphones (USB Audio)" in notes[0] and "\n" not in notes[0]


def test_connected_or_unknown_voice_device_is_passed_on_without_notice():  # #448
    s = dict(core.DEFAULTS, voice=True, voice_device="Headphones (USB Audio)")
    notes = []
    for outputs in (["Headphones (USB Audio)"], [], None):  # [] / None: no audio stack, the CLI decides
        a = cli.parse_args(core.settings_to_argv(s, outputs=outputs, notice=notes.append))
        assert a.voice_device == "Headphones (USB Audio)"
    assert notes == []
