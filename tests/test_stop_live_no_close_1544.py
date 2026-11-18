"""#1544: a live source without close() (speak_selftest's ClipSource) must not log a swallowed cleanup error."""
import threading
from types import SimpleNamespace

from tinglongwu import diag
from tinglongwu.reverse.session import _stop_live


class NoClose:
    pass


class WithClose:
    closed = False

    def close(self):
        self.closed = True


def _call(monkeypatch, source):
    seen = []
    monkeypatch.setattr(diag, "swallowed", lambda name: seen.append(name))
    stop = threading.Event()
    cleanup = _stop_live(stop, [], None, SimpleNamespace(enabled=False), SimpleNamespace(enabled=False), source)
    cleanup()
    return stop, seen


def test_source_without_close_is_not_a_swallowed_error(monkeypatch):
    stop, seen = _call(monkeypatch, NoClose())
    assert stop.is_set() and seen == []


def test_source_with_close_is_still_closed(monkeypatch):
    src = WithClose()
    _call(monkeypatch, src)
    assert src.closed
