"""#1001: one first-screen Start runs listen (PC sound -> my language) and speak (mic -> other side) together."""
import threading

import pytest

from tinglongwu.gui import core
from tinglongwu.i18n import MESSAGES as STRINGS
from tinglongwu.sink import Result


# -- pure helpers ------------------------------------------------------------------------------------------------

def test_both_settings_builds_the_two_legs_of_session_route():
    saved = dict(core.DEFAULTS, target="zh-tw", lang="en", live_draft=True, save=True)
    listen, speak = core.both_settings(saved)
    assert (listen["source"], listen["lang"], listen["target"]) == ("system", "en", "zh-tw")
    assert (speak["source"], speak["lang"], speak["target"]) == ("mic", "zh", "en")
    assert listen["live_draft"] is True and listen["save"] is True  # listen keeps the saved choices
    assert speak["live_draft"] is False and speak["save"] is False  # one draft row, one save file
    assert saved["source"] == core.DEFAULTS["source"]  # the saved settings are not changed


def test_both_settings_refuses_the_same_language_on_both_sides():
    with pytest.raises(core.SameLanguageError):
        core.both_settings(dict(core.DEFAULTS, target="zh-tw", lang="zh"))


@pytest.mark.parametrize("phase,active,want", [
    ("idle", None, ("gui_quick_both", True)),
    ("loading", core.BOTH_MODE, ("gui_loading", False)),
    ("running", core.BOTH_MODE, ("gui_stop", True)),
    ("running", "listen", ("gui_quick_both", False)),
    ("busy", core.BOTH_MODE, ("gui_quick_both", False)),
])
def test_start_button_follows_the_session(phase, active, want):
    assert core.start_button(phase, active) == want


def test_speak_lines_join_the_list_without_touching_the_listen_draft():
    import queue

    q = queue.Queue()
    sink = core.SpeakQueueSink(q)
    sink.write_source("zh", "你好")  # no "heard" placeholder from the speak leg
    sink.write(Result(src="zh", original="你好", pivot=None, target="Hello", tgt="en", side="me"))
    st = core.SubtitleState(4)
    st.apply(("draft", "how are", "你好嗎"))  # the listen leg's live draft
    ev = q.get_nowait()
    assert q.empty() and ev[0] == "final_side"
    assert st.apply(ev)
    assert st.lines() == [(core.SPEAK_MARK + "Hello", False), ("你好嗎", True)]


@pytest.mark.parametrize("lang", sorted(STRINGS))
def test_new_strings_exist_in_every_ui_language(lang):
    for key in ("gui_quick_both", "gui_ready_both", "gui_both_no_voice"):
        assert STRINGS[lang][key] and "\n" not in STRINGS[lang][key]


# -- on a real App -------------------------------------------------------------------------------------------------

@pytest.fixture
def both_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n, vbcable
    from tinglongwu.gui import app, voice_install

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("TLWU_BOTH", raising=False)
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    monkeypatch.setattr(app.pack_on_demand, "first_missing", lambda *a, **k: None)
    monkeypatch.setattr(app, "missing_start_model", lambda run: None)
    monkeypatch.setattr(voice_install, "offer_needed", lambda run, mdir: False)
    monkeypatch.setattr(vbcable, "needs_offer", lambda names, platform=None: False)
    cable = {"name": "CABLE Input (VB-Audio Virtual Cable)"}
    monkeypatch.setattr(app, "meeting_cable", lambda: cable["name"])
    legs, envs = [], []
    lock = threading.Lock()

    def fake_leg(self, s, stop, gate=None, sink_cls=None):
        import os

        with lock:
            legs.append((dict(s), sink_cls, gate))
            envs.append(os.environ.get("TLWU_BOTH"))
        stop.wait(10)

    monkeypatch.setattr(app.App, "_run_leg", fake_leg)
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    a = app.App()
    a.root.update()
    yield a, legs, envs, cable
    a.stop_ev.set()
    if a.worker is not None:
        a.worker.join(5)
    a.root.destroy()


def _wait(a, cond):
    for _ in range(100):
        if cond():
            return True
        a.root.after(20)
        a.root.update()
    return cond()


def test_one_start_runs_both_legs_and_one_stop_ends_both(both_app):
    import os

    from tinglongwu import cli

    a, legs, envs, _ = both_app
    a.v_target.set("zh-tw")
    a.v_lang.set("en")
    assert a.both_btn.cget("text") == "Start" and str(a.both_btn.cget("state")) == "normal"
    a.both_btn.invoke()
    assert _wait(a, lambda: len(legs) == 2)
    by_src = {s["source"]: (s, cls, gate) for s, cls, gate in legs}
    lis, _, lis_gate = by_src["system"]
    spk, spk_cls, spk_gate = by_src["mic"]
    assert (lis["lang"], lis["target"]) == ("en", "zh-tw") and lis_gate is None
    assert (spk["lang"], spk["target"]) == ("zh", "en") and spk_cls is core.SpeakQueueSink
    assert spk["voice"] is True and spk["speak_cable"] is True and spk_gate is not None  # #710 voice into CABLE
    assert envs == ["1", "1"]  # #250: the CPU budget is split while both legs run
    assert str(a.both_btn.cget("state")) == "disabled"  # "Loading..." until ready (#332)
    a.q.put(("status", f"{cli.READY_STATUS} - loaded in 1.0 s"))
    a.poll()
    assert a.v_status.get().startswith("Ready - play the video or speak")
    assert a.both_btn.cget("text") == "Stop" and str(a.both_btn.cget("state")) == "normal"
    worker = a.worker
    a.both_btn.invoke()  # one Stop
    worker.join(5)
    assert not worker.is_alive()
    assert not [th for th in threading.enumerate() if th.name in ("tlwu-listen", "tlwu-speak")]  # no orphan leg
    a.poll()
    assert a.both_btn.cget("text") == "Start" and a.quick_mode is None and not a.listening
    assert os.environ.get("TLWU_BOTH") is None  # restored after the run


def test_without_cable_both_still_starts_with_speak_subtitles(both_app, monkeypatch):
    from tinglongwu.gui import app

    a, legs, _, cable = both_app
    cable["name"] = None
    monkeypatch.setattr(app, "speak_route", lambda run, c: core.speak_route(run, c, platform="win32"))
    a.v_target.set("zh-tw")
    a.v_lang.set("en")
    a.toggle(core.BOTH_MODE)
    assert _wait(a, lambda: len(legs) == 2)
    spk = next(s for s, _, _ in legs if s["source"] == "mic")
    assert spk["voice"] is False  # subtitles only, no crash
    assert a.v_notice.get().startswith("Speak: subtitles only")


def test_declining_the_cable_offer_still_starts_both(both_app, monkeypatch):
    from tinglongwu import vbcable
    from tinglongwu.gui import app

    a, legs, _, cable = both_app
    cable["name"] = None
    monkeypatch.setattr(vbcable, "needs_offer", lambda names, platform=None: True)
    asked = []
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *x, **k: asked.append(1) or False)
    a.v_target.set("zh-tw")
    a.v_lang.set("en")
    a.toggle(core.BOTH_MODE)
    assert _wait(a, lambda: len(legs) == 2) and asked == [1]  # asked once, then both legs run


def test_one_leg_failing_leaves_the_other_running_until_stop(both_app, monkeypatch):
    from tinglongwu.gui import app

    a, legs, _, _ = both_app
    ran = []

    def leg(self, s, stop, gate=None, sink_cls=None):
        if s["source"] == "system":
            self.q.put(("error", "no loopback"))
            return
        ran.append(s["source"])
        stop.wait(10)

    monkeypatch.setattr(app.App, "_run_leg", leg)
    a.v_target.set("zh-tw")
    a.v_lang.set("en")
    a.toggle(core.BOTH_MODE)
    assert _wait(a, lambda: ran == ["mic"])
    a.poll()
    assert a.errored and a.worker.is_alive()  # speak keeps going
    a.stop_ev.set()
    a.worker.join(5)
    a.poll()
    assert not a.listening
