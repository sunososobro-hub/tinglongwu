"""#1591: real-Tk tests are skipped, not segfaulted, on macOS + Tk 9."""
import pytest

from conftest import install_tk_guard, real_tk_unsafe


def test_unsafe_only_on_mac_tk9():
    assert real_tk_unsafe("darwin", 9.1)
    assert real_tk_unsafe("darwin", "9.0")
    assert not real_tk_unsafe("darwin", 8.6)
    assert not real_tk_unsafe("linux", 9.1)
    assert not real_tk_unsafe("win32", 9.1)


def test_tk_root_is_skipped_when_unsafe(monkeypatch):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(tk, "TkVersion", 9.1)
    assert install_tk_guard(monkeypatch, platform="darwin")
    with pytest.raises(pytest.skip.Exception, match="segfault, #1591"):
        tk.Tk()


def test_guard_not_installed_on_other_platforms(monkeypatch):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(tk, "TkVersion", 9.1)
    assert not install_tk_guard(monkeypatch, platform="linux")
