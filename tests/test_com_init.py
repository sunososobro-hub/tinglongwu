import sys
import threading
import types

from tinglongwu.capture import _soundcard as sc


class FakeOle32:
    def __init__(self, hr=0):
        self.calls, self.hr = [], hr

    def CoInitializeEx(self, reserved, flags):
        self.calls.append(("init", threading.current_thread().name, flags))
        return self.hr

    def CoUninitialize(self):
        self.calls.append(("uninit", threading.current_thread().name))


def _run_in_thread(monkeypatch, hr, platform="win32"):
    ole = FakeOle32(hr)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sc, "_load_ole32", lambda: ole)

    def work():
        with sc.com_initialized():
            ole.calls.append(("body",))
    t = threading.Thread(target=work, name="worker")
    t.start(); t.join()
    return ole.calls


def test_worker_thread_initializes_and_releases_com(monkeypatch):
    calls = _run_in_thread(monkeypatch, 0)
    assert calls == [("init", "worker", 0), ("body",), ("uninit", "worker")]


def test_s_false_still_uninitializes(monkeypatch):
    assert ("uninit", "worker") in _run_in_thread(monkeypatch, 1)


def test_changed_mode_is_not_an_error_and_no_uninit(monkeypatch):
    calls = _run_in_thread(monkeypatch, 0x80010106)
    assert ("body",) in calls and not any(c[0] == "uninit" for c in calls)


def test_non_windows_noop(monkeypatch):
    assert _run_in_thread(monkeypatch, 0, platform="linux") == [("body",)]


def test_frames_runs_inside_com_scope(monkeypatch):
    order = []
    ole = FakeOle32(0)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sc, "_load_ole32", lambda: ole)
    monkeypatch.setitem(sys.modules, "soundcard", types.ModuleType("soundcard"))

    class Src(sc.SoundcardSource):
        def _pick_microphone(self, s):
            order.append(len(ole.calls))
            raise RuntimeError("stop")
    try:
        list(Src().frames())
    except RuntimeError:
        pass
    assert order == [1] and ole.calls[-1][0] == "uninit"
