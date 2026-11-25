"""#734 install GIF: the sped-up whole-clip GIF (readme_gif.py make --whole) and the xterm install driver."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rg = _load("readme_gif")
ri = _load("readme_gif_install")


def test_speed_for_fits_the_whole_clip_and_never_slows_down():
    assert rg.speed_for(120.0, 14.9) * 14.9 >= 120.0
    assert rg.speed_for(2.0, 14.9) == 1.0
    with pytest.raises(ValueError):
        rg.speed_for(10.0, 0)


def test_gif_cmd_speed_reads_more_input_and_adds_setpts():
    cmd = rg.gif_cmd(Path("in.mp4"), Path("out.gif"), 0, 14.9, 8, 560, speed=8.0)
    assert float(cmd[cmd.index("-t") + 1]) == pytest.approx(14.9 * 8)
    vf = cmd[cmd.index("-filter_complex") + 1]
    assert vf.startswith("setpts=PTS/8,fps=8")
    plain = rg.gif_cmd(Path("in.mp4"), Path("out.gif"), 0, 2.9, 10, 560)
    assert "setpts" not in plain[plain.index("-filter_complex") + 1]


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")
def test_make_whole_squeezes_a_long_run_into_the_install_caps(tmp_path):
    src = tmp_path / "install.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=800x600:rate=10", "-t", "60",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    out = tmp_path / "install_15s.gif"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "readme_gif.py"), "make", str(src), str(out),
                        "--max-seconds", "15", "--max-mb", "5", "--whole"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    frames, dur, _w, _h = rg.gif_info(out)
    # all 60 s are in it (about 4x faster), and it stays within the #734 install caps
    assert 13.0 <= dur <= 15.0 and frames > 50 and out.stat().st_size <= 5 * rg.MB
    assert [ok for name, ok, _ in rg.check(tmp_path, present_only=True)] == [True]


def test_shell_line_runs_the_installer_unattended_and_keeps_its_exit_code(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "install.sh").write_text(
        'echo "ref=$TLWU_REF dir=$TLWU_DIR yes=$TLWU_YES skip=$TLWU_SKIP_MODELS"\n'
        '[ -t 0 ] && echo "stdin is a tty"\nexit 7\n')
    target = tmp_path / "work" / "tinglongwu"
    target.parent.mkdir()
    r = subprocess.run(["bash", "-c", ri.shell_line(repo, "readme-gif-install", target, hold=0)],
                       capture_output=True, text=True)
    assert r.returncode == 7
    lines = r.stdout.splitlines()
    assert lines[0] == f"$ {ri.SHOWN}"
    assert lines[1] == f"ref=readme-gif-install dir={target} yes=1 skip=1"
    assert "stdin is a tty" not in r.stdout
    assert (tmp_path / "work" / "rc").read_text().strip() == "7"
    assert "TLWU_YES=1" in ri.SHOWN and "TLWU_SKIP_MODELS=1" in ri.SHOWN  # the prompt shows what really runs


def test_xterm_cmd_runs_the_line_in_a_titled_window():
    cmd = ri.xterm_cmd("echo hi")
    assert cmd[0] == "xterm" and cmd[cmd.index("-T") + 1] == ri.TITLE and cmd[-3:] == ["bash", "-c", "echo hi"]


XWININFO = """
xwininfo: Window id: 0x400022 "tlwu-install"

  Absolute upper-left X:  1
  Absolute upper-left Y:  21
  Relative upper-left X:  0
  Relative upper-left Y:  0
  Width: 705
  Height: 423
"""


def test_window_rect_and_even_rect():
    assert ri.window_rect(XWININFO) == (1, 21, 705, 423)
    assert ri.even_rect(-3, 21, 705, 423) == (0, 21, 704, 422)
    with pytest.raises(ValueError):
        ri.window_rect("xwininfo: error")
