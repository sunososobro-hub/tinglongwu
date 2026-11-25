"""#734: README demo GIFs - the stdlib GIF reader, the size/duration caps and the fps/width ladder."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("readme_gif", ROOT / "scripts" / "readme_gif.py")
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)


def tiny_gif(path: Path, delays_cs: list[int], w: int = 4, h: int = 3, pad: int = 0) -> Path:
    """A valid GIF89a with one 1x1 frame per delay (global 2-colour table, a NETSCAPE loop block)."""
    b = bytearray(b"GIF89a" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([0x80, 0, 0]) + bytes(6))
    b += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    for d in delays_cs:
        b += bytes([0x21, 0xF9, 4, 0]) + d.to_bytes(2, "little") + b"\x00\x00"
        b += b"\x2c" + bytes(4) + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + b"\x00"
        b += b"\x02\x02\x44\x01\x00"
    if pad:  # a comment extension makes the file bigger without changing frames or duration
        b += b"\x21\xfe" + (b"\xff" + bytes(255)) * (pad // 255 + 1) + b"\x00"
    b += b"\x3b"
    path.write_bytes(bytes(b))
    return path


def test_gif_info_sums_frame_delays(tmp_path):
    assert rg.gif_info(tiny_gif(tmp_path / "a.gif", [10, 10, 25])) == (3, 0.45, 4, 3)


def test_gif_info_reads_the_shipped_demo_gif():
    frames, dur, w, h = rg.gif_info(ROOT / "assets" / "demo" / "listen_en_to_zh.gif")
    assert frames > 10 and dur > 1 and w > 100 and h > 50


def test_gif_info_rejects_non_gif(tmp_path):
    (tmp_path / "x.gif").write_bytes(b"PNG....")
    with pytest.raises(ValueError):
        rg.gif_info(tmp_path / "x.gif")


def test_check_flags_missing_too_long_and_too_big(tmp_path):
    tiny_gif(tmp_path / "mode_listen.gif", [100, 100, 100])           # 3.0 s: ok
    tiny_gif(tmp_path / "mode_speak.gif", [100, 100, 101])            # 3.01 s: too long
    tiny_gif(tmp_path / "mode_two_way.gif", [50], pad=2 * rg.MB + 1)  # over 2 MB
    rows = {n: (ok, d) for n, ok, d in rg.check(tmp_path)}
    assert rows["mode_listen.gif"][0] is True
    assert rows["mode_speak.gif"][0] is False and "3.01 s" in rows["mode_speak.gif"][1]
    assert rows["mode_two_way.gif"][0] is False
    assert rows["install_15s.gif"] == (False, "missing")
    assert "install_15s.gif" not in {n for n, _, _ in rg.check(tmp_path, present_only=True)}


def test_check_cli_exit_codes(tmp_path):
    for n in rg.CAPS:
        tiny_gif(tmp_path / n, [100])
    assert rg.main(["check", "--dir", str(tmp_path)]) == 0
    tiny_gif(tmp_path / "install_15s.gif", [1600])
    assert rg.main(["check", "--dir", str(tmp_path)]) == 1


def test_fit_steps_down_until_under_the_cap():
    tried = []

    def encode(fps, width):
        tried.append((fps, width))
        return fps * width * 300  # bytes grow with fps and width

    assert rg.fit(encode, 8 * 560 * 300) == (8, 560)
    assert tried == list(rg.LADDER[:4])
    assert rg.fit(lambda f, w: 10**12, 1) is None


def test_gif_cmd_trims_and_uses_a_palette():
    cmd = rg.gif_cmd(Path("in.mp4"), Path("out.gif"), 1.5, 2.9, 10, 560)
    assert cmd[cmd.index("-ss") + 1] == "1.5" and cmd[cmd.index("-t") + 1] == "2.9"
    vf = cmd[cmd.index("-filter_complex") + 1]
    assert "fps=10" in vf and "scale=560" in vf and "palettegen" in vf and "paletteuse" in vf


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_make_real_video_fits_the_mode_caps(tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15", "-t", "6",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    out = tmp_path / "mode_listen.gif"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "readme_gif.py"), "make", str(src), str(out),
                        "--max-seconds", "3", "--max-mb", "2", "--start", "1"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    frames, dur, w, h = rg.gif_info(out)
    assert frames > 10 and 2.5 <= dur <= 3.0 and w <= 720 and out.stat().st_size <= 2 * rg.MB
    assert rg.check(tmp_path, present_only=True) == [("mode_listen.gif", True, rg.check(tmp_path, True)[0][2])]
