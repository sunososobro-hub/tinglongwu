"""#1028 mic toggle: muting stops what the speak leg's capture hears while the listen leg keeps running."""
import threading

import numpy as np
import pytest

from tinglongwu import cli
from tinglongwu.capture.base import MuteSource
from tinglongwu.gui import core
from tinglongwu.i18n import MESSAGES as STRINGS

SR = 16000


class Src:
    def __init__(self, n=4):
        self.n, self.closed, self.gen_closed = n, False, False

    def frames(self):
        try:
            for i in range(self.n):
                yield np.full(SR // 10, 0.5 + i, dtype=np.float32)
        finally:
            self.gen_closed = True

    def close(self):
        self.closed = True


def test_mute_source_silences_frames_only_while_muted():
    muted = threading.Event()
    inner = Src()
    src = MuteSource(inner, muted)
    out = []
    for i, f in enumerate(src.frames()):
        out.append(f)
        if i == 0:
            muted.set()  # mic off after the first frame
        elif i == 2:
            muted.clear()  # mic back on
    assert [len(f) for f in out] == [SR // 10] * 4  # same length: the stream clock does not jump
    assert [float(f.max()) for f in out] == [0.5, 0.0, 0.0, 3.5]
    assert inner.gen_closed
    src.close()
    assert inner.closed


def test_mute_source_closes_the_capture_generator_on_early_exit():
    inner = Src(10)
    gen = MuteSource(inner, threading.Event()).frames()
    next(gen)
    gen.close()
    assert inner.gen_closed


def _session_source(tmp_path, monkeypatch, mute):
    """Run cli.run_session with fake capture/ASR and return the audio source the pipeline got."""
    import tinglongwu.asr
    import tinglongwu.pipeline

    got = {}
    monkeypatch.setattr(tinglongwu.pipeline, "run", lambda source, *a, **k: got.setdefault("source", source))
    monkeypatch.setattr(tinglongwu.asr, "FasterWhisperRecognizer", lambda *a, **k: type("A", (), {"device": "cpu"})())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: Src())
    monkeypatch.setattr(cli, "models_dir", lambda d=None: tmp_path)
    monkeypatch.setattr("tinglongwu.minspec.check_avx2", lambda *a, **k: None)
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    monkeypatch.chdir(tmp_path)
    a = cli.parse_args(["--transcript-only", "--no-adapt", "--model", "tiny", "--device", "cpu", "--mic"])
    a.mic_mute = mute
    cli.run_session(a)
    return got["source"]


def test_run_session_wraps_only_a_leg_that_has_a_mute_event(tmp_path, monkeypatch):
    plain = _session_source(tmp_path, monkeypatch, None)
    assert isinstance(plain, Src)  # the listen leg (no mute event) gets its capture unchanged
    muted = threading.Event()
    src = _session_source(tmp_path, monkeypatch, muted)
    assert isinstance(src, MuteSource)
    muted.set()
    assert all(float(abs(f).max()) == 0.0 for f in src.frames())


@pytest.mark.parametrize("phase,active,off,want", [
    ("idle", None, False, ("gui_mic_on", False)),
    ("loading", core.BOTH_MODE, False, ("gui_mic_on", False)),
    ("running", core.BOTH_MODE, False, ("gui_mic_on", True)),
    ("running", core.BOTH_MODE, True, ("gui_mic_off", True)),
    ("running", "listen", False, ("gui_mic_on", False)),
])
def test_mic_button_follows_the_session(phase, active, off, want):
    assert core.mic_button(phase, active, off) == want


@pytest.mark.parametrize("lang", sorted(STRINGS))
def test_mic_strings_exist_in_every_ui_language(lang):
    for key in ("gui_mic_on", "gui_mic_off"):
        assert STRINGS[lang][key] and "\n" not in STRINGS[lang][key]


# -- on a real App -------------------------------------------------------------------------------------------------

@pytest.fixture
def mic_app(tmp_path, monkeypatch):
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
    monkeypatch.setattr(app, "meeting_cable", lambda: "CABLE Input (VB-Audio Virtual Cable)")
    monkeypatch.setattr(app, "voice_outputs", lambda names=None: [])
    legs = {}
    lock = threading.Lock()

    def fake_session(a, sink=None, draft_factory=None, stop=None, **_k):
        with lock:
            legs["mic" if a.mic else "system"] = a
        stop.wait(10)

    monkeypatch.setattr(cli, "run_session", fake_session)
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    a = app.App()
    a.root.update()
    yield a, legs
    a.stop_ev.set()
    if a.worker is not None:
        a.worker.join(5)
    a.root.destroy()


def test_mic_toggle_mutes_the_speak_leg_and_leaves_listening_running(mic_app):
    a, legs = mic_app
    a.v_target.set("zh-tw")
    a.v_lang.set("en")
    assert str(a.mic_btn.cget("state")) == "disabled"  # nothing to mute before Start
    a.both_btn.invoke()
    for _ in range(100):
        if len(legs) == 2:
            break
        a.root.after(20)
        a.root.update()
    assert set(legs) == {"system", "mic"}
    assert legs["system"].mic_mute is None  # the listen leg cannot be muted
    mute = legs["mic"].mic_mute
    assert mute is a.mic_off and not mute.is_set()
    a.q.put(("status", f"{cli.READY_STATUS} - loaded in 1.0 s"))
    a.poll()
    assert a.mic_btn.cget("text") == "Mic: on (others hear me)" and str(a.mic_btn.cget("state")) == "normal"
    a.mic_btn.invoke()
    assert mute.is_set() and a.mic_btn.cget("text") == "Mic: off (still listening)"
    assert a.worker.is_alive() and not a.stop_ev.is_set()  # the session (listen leg) keeps running
    a.mic_btn.invoke()
    assert not mute.is_set()
    a.mic_btn.invoke()
    worker = a.worker
    a.both_btn.invoke()  # Stop
    worker.join(5)
    a.poll()
    a.both_btn.invoke()  # the next session starts with the mic on again
    for _ in range(50):
        if a.mic_off is not mute:
            break
        a.root.update()
    assert not a.mic_off.is_set()
