"""#1736: the GPU pack downloads in the background by default; Cancel stops it and is remembered (fake downloader)."""
from __future__ import annotations

import pytest

from tinglongwu.gui import gpu_offer as G
from tinglongwu.settings import DEFAULTS


@pytest.mark.parametrize("state,cpu,dismissed,offline,auto_off,want", [
    (G.MISSING, False, False, False, False, True),
    (G.OLD, False, False, False, False, True),
    (G.MISSING, False, False, False, True, False),  # cancelled before: only the one-click offer
    (G.INSTALLED, False, False, False, False, False),
    (G.UNAVAILABLE, False, False, False, False, False),
    (G.MISSING, True, False, False, False, False),  # CPU mode
    (G.MISSING, False, True, False, False, False),  # "Don't ask again"
    (G.MISSING, False, False, True, False, False),  # TLWU_OFFLINE=1
])
def test_should_autostart(state, cpu, dismissed, offline, auto_off, want):
    assert G.should_autostart(state, cpu, dismissed, offline, auto_off) is want


def test_auto_off_default_is_false():
    assert DEFAULTS[G.AUTO_OFF_KEY] is False


class FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


def make(rc=0, lines=(), cancel_midway=False):
    events, saved, settings = [], [], {}
    box = {}

    def run(name, put, on_start=None, **kw):
        proc = FakeProc()
        box["proc"] = proc
        if on_start:
            on_start(proc)
        if cancel_midway:
            box["auto"].cancel()
        for ln in lines:
            put(("pack_line", ln))
        put(("pack_done", name, rc))
        return rc

    auto = G.AutoInstall(run, settings, lambda: saved.append(dict(settings)))
    box["auto"] = auto
    return auto, events, saved, settings, box


def test_success_releases_lock_and_reports_ok():
    auto, events, saved, settings, box = make(rc=0)
    G.INSTALLING.set()
    assert auto.run_install(events.append, cmd=["x"]) == 0
    assert not G.INSTALLING.is_set()
    assert auto.outcome(0, []) == "ok"
    assert not settings and not saved  # nothing remembered on success


def test_cancel_stops_child_and_is_remembered():
    auto, events, saved, settings, box = make(rc=1, cancel_midway=True)
    G.INSTALLING.set()
    auto.run_install(events.append, cmd=["x"])
    assert box["proc"].terminated
    assert settings[G.AUTO_OFF_KEY] is True and saved
    assert auto.outcome(1, []) == "cancelled"
    assert not G.INSTALLING.is_set()
    # next start: no automatic download any more, the offer banner stays
    assert not G.should_autostart(G.MISSING, False, False, False, settings[G.AUTO_OFF_KEY])
    assert G.should_offer(G.MISSING, False, False, False)


def test_cancel_before_child_started_still_stops_it():
    auto = G.AutoInstall(lambda *a, **k: 0, {}, lambda: None)
    auto.cancel()
    p = FakeProc()
    auto.on_start(p)
    assert p.terminated


def test_failure_is_failed_not_remembered():
    auto, events, saved, settings, box = make(rc=2, lines=["gpu pack: FAIL network"])
    assert auto.run_install(events.append, cmd=["x"]) == 2
    assert auto.outcome(2, ["gpu pack: FAIL network"]) == "failed"
    assert not settings  # a failure does not switch auto-download off
    G.INSTALLING.clear()


def test_no_pack_release_is_failed():
    auto, *_ = make(rc=0)
    assert auto.outcome(0, [G.NO_PACK_PREFIX + " (x)"]) == "failed"


def test_cancel_survives_unwritable_settings():
    def boom():
        raise OSError("ro")

    auto = G.AutoInstall(lambda *a, **k: 0, {}, boom)
    auto.cancel()
    assert auto.cancelled and auto.settings[G.AUTO_OFF_KEY] is True
