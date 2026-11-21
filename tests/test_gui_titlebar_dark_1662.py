import sys
from unittest import mock

from tinglongwu.gui import app


class _W:
    def update_idletasks(self): pass
    def winfo_id(self): return 5


def test_noop_off_windows():
    with mock.patch.object(sys, "platform", "linux"):
        assert app.set_titlebar_dark(_W(), True) is False


def test_dark_and_light_values():
    calls = []
    fake = mock.MagicMock()
    fake.windll.user32.GetParent.return_value = 99

    def dwm(h, attr, ref, size):
        calls.append((h, attr, ref.value))
        return 0
    fake.windll.dwmapi.DwmSetWindowAttribute.side_effect = dwm
    fake.byref = lambda x: x
    fake.sizeof = lambda x: 4
    import ctypes
    fake.c_int = ctypes.c_int
    with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(sys.modules, {"ctypes": fake}):
        assert app.set_titlebar_dark(_W(), True)
        assert app.set_titlebar_dark(_W(), False)
    assert calls == [(99, 20, 1), (99, 20, 0)]
