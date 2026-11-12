"""#270: the packaged app icon loads, is a valid multi-size .ico/.png, and the GUI applies it."""
import struct
import sys

import pytest

from tinglongwu.gui import icon


def test_ico_has_required_sizes():
    data = icon.icon_path("tinglongwu.ico").read_bytes()
    reserved, kind, n = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = {(data[6 + 16 * i] or 256) for i in range(n)}
    assert {16, 32, 48, 256} <= sizes


def test_png_and_svg_resources_load():
    assert icon.icon_path("tinglongwu.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert icon.icon_path("tinglongwu.svg").read_text("utf-8").lstrip().startswith("<svg")


def test_apply_icon_sets_iconphoto():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        icon.apply_icon(root)
        assert getattr(root, "_tlwu_icon", None) is not None
    finally:
        root.destroy()


def test_set_app_id_is_safe_off_windows():
    if sys.platform != "win32":
        assert icon.set_app_id() is None
