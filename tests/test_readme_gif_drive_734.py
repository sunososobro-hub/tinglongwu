"""#734 part 2: the GUI driver that records the per-mode README GIFs (pure parts; the live run is the
readme-gifs workflow on a runner with Xvfb, PulseAudio and the models)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("readme_gif_drive", ROOT / "scripts" / "readme_gif_drive.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


def test_every_mode_makes_one_capped_gif():
    assert {gif for _arg, gif in rd.MODES.values()} == {"mode_listen.gif", "mode_speak.gif", "mode_two_way.gif"}
    assert all(gif in rd.rg.CAPS for _arg, gif in rd.MODES.values())
    from tinglongwu.gui.core import BOTH_MODE

    assert rd.MODES["two-way"][0] == BOTH_MODE  # the one Start: listen + speak together


@pytest.mark.parametrize("mode,scene", [("listen", "video"), ("speak", "meeting"), ("two-way", "meeting")])
def test_gui_settings_survive_load_settings(tmp_path, mode, scene):
    """Every preset key is a real gui.json setting with the right type: load_settings keeps it."""
    from tinglongwu.settings import load_settings

    p = tmp_path / "gui.json"
    p.write_text(json.dumps(rd.gui_settings(mode)), "utf-8")
    s = load_settings(p, gpu=lambda: False)
    assert (s["lang"], s["target"], s["scene"]) == ("en", "zh-tw", scene)
    assert s["tour_seen"] is True and s["usage_asked"] is True and s["floating_bar"] is False
    assert {k: s[k] for k in rd.gui_settings(mode)} == rd.gui_settings(mode)


def test_clips_per_mode(tmp_path):
    zh = tmp_path / "zh.wav"
    assert rd.clips("listen", None) == [rd.CLIP_EN] and rd.CLIP_EN.exists()
    assert rd.clips("speak", zh) == [zh]
    assert rd.clips("two-way", zh) == [rd.CLIP_EN, zh]
    with pytest.raises(SystemExit):
        rd.clips("speak", None)


def test_region_is_clipped_and_even():
    assert rd.region(10, 20, 461, 641, 1920, 1080) == (10, 20, 460, 640)
    assert rd.region(-5, 900, 500, 400, 1920, 1080) == (0, 900, 500, 180)
    with pytest.raises(ValueError):
        rd.region(1919, 0, 300, 300, 1920, 1080)


def test_gif_start_puts_the_first_line_inside_the_gif():
    assert rd.gif_start(5.0, 12.0) == 3.0          # 2 s before the line, 1 s after it
    assert rd.gif_start(0.8, 12.0) == 0.0          # never before the recording
    assert rd.gif_start(11.5, 12.0) == 9.0         # the 3 s still fit in the recording
    assert rd.gif_start(None, 12.0) is None        # nothing translated: no GIF (no empty demo)
    assert rd.gif_start(13.0, 12.0) is None


def test_translated_counts_only_rows_with_a_translation():
    from tinglongwu.gui.core import SubtitleState

    s = SubtitleState(5)
    s.finals = [("hello", "你好"), ("pending", None), ("x", "")]
    assert rd.translated(s) == 1
    assert rd.translated(object()) == 0
    s.finals = []
    s.draft = ("hel", "你")  # live draft: translated text on screen before any final
    assert rd.translated(s) == 1
    s.draft, s.partial = ("hel", ""), {"pending": "部分"}
    assert rd.translated(s) == 1


def test_make_gif_failure_is_exit_1_not_an_exception(tmp_path, monkeypatch, capsys):
    def boom(_argv):
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    monkeypatch.setattr(rd.rg, "main", boom)
    assert rd.make_gif(tmp_path / "v.mp4", tmp_path / "mode_listen.gif", 1.0) == 1
    assert "FAIL mode_listen.gif" in capsys.readouterr().out


def test_record_cmd_grabs_the_window_rectangle(tmp_path):
    cmd = rd.rg.record_cmd(tmp_path / "a.mp4", 12, ":99", "460x640", origin=(10, 20))
    assert cmd[cmd.index("-i") + 1] == ":99+10,20" and cmd[cmd.index("-video_size") + 1] == "460x640"
    assert rd.rg.record_cmd(tmp_path / "a.mp4", 1, ":1", "8x8")[cmd.index("-i") + 1] == ":1+0,0"


@pytest.mark.skipif(sys.platform == "win32", reason="sh + a fake paplay script")
def test_play_script_plays_each_clip_in_order(tmp_path):
    log = tmp_path / "log.txt"
    fake = tmp_path / "paplay"
    fake.write_text(f'#!/bin/sh\necho "$1" >> "{log}"\n')
    fake.chmod(0o755)
    clips = [tmp_path / "a b.wav", tmp_path / "zh.wav"]
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    subprocess.run(rd.play_script(clips), env=env, check=True, timeout=10)
    assert log.read_text().splitlines() == [str(c) for c in clips]


def test_cli_rejects_unknown_mode(capsys):
    with pytest.raises(SystemExit):
        rd.main(["sing", "--out-dir", "x"])
