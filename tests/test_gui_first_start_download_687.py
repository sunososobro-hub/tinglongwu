"""#739 (#687 part 3): a missing speech model downloads on the first Start with a progress bar, then the session starts."""
import pytest

from tinglongwu.gui import core


def test_parse_progress_takes_last_percent():
    assert core.parse_progress("fetch 10%\rfetch 55%") == 55
    assert core.parse_progress("Downloading 100 %") == 100
    assert core.parse_progress("no number here") is None


def test_progress_reaches_100_and_never_goes_back():
    sd = core.StartDownload("medium")
    for ev in [("dl_line", "x 10%"), ("dl_line", "x 60%"), ("dl_line", "x 30%"), ("dl_done", "medium", 0)]:
        sd.feed(ev)
        assert sd.percent >= 10
    assert sd.percent == 100 and sd.ok and sd.done and sd.error == ""


def test_failure_is_one_error_line():
    sd = core.StartDownload("medium")
    for ev in [("dl_line", "x 40%"), ("dl_line", "ConnectionError: no route"), ("dl_done", "medium", 1)]:
        sd.feed(ev)
    assert sd.done and not sd.ok and sd.error == "ConnectionError: no route" and "\n" not in sd.error


def test_missing_start_model_only_for_manual_quality(tmp_path):
    (tmp_path / "whisper-small").mkdir()
    assert core.missing_start_model({"quality": "manual", "model": "medium"}, tmp_path) == "medium"
    assert core.missing_start_model({"quality": "manual", "model": "small"}, tmp_path) is None
    assert core.missing_start_model({"quality": "auto", "model": "medium"}, tmp_path) is None


@pytest.fixture
def gui(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app

    mdir = tmp_path / "models"
    mdir.mkdir()
    monkeypatch.setenv("TLWU_MODELS", str(mdir))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    monkeypatch.setattr(app, "save_settings", lambda s: None)
    monkeypatch.setattr(app.pack_on_demand, "first_missing", lambda *a, **k: None)
    runs = []

    def fake_work(self, s, stop):
        runs.append(dict(s))
        self.q.put(("stopped",))

    monkeypatch.setattr(app.App, "_work", fake_work)
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    a = app.App()
    a.root.update()
    a.v_quality.set("manual")
    a.v_model.set("medium")
    yield a, app, mdir, runs
    a.stop_ev.set()
    try:
        a.root.destroy()
    except tk.TclError:
        pass


def _pump(a, until, n=300):
    import time

    for _ in range(n):
        a.root.update()
        if until():
            return True
        time.sleep(0.01)
    return False


def test_start_downloads_with_progress_then_starts_without_second_click(gui, monkeypatch):
    a, app, mdir, runs = gui
    seen = []

    def fake_download(model, put, *args, **kw):
        for pct in (10, 50, 100):
            put(("dl_line", f"fetch {pct}%"))
        (mdir / f"whisper-{model}").mkdir()
        put(("dl_done", model, 0))
        return 0

    orig_event = a._dl_event
    a._dl_event = lambda ev: (orig_event(ev), seen.append(a.v_dl_pct.get()))
    monkeypatch.setattr(app, "run_download", fake_download)
    a.poll()  # starts the self-rescheduling queue pump (App.run() does this in the real app)
    a.toggle()
    assert a.downloading == "medium" and runs == []
    assert _pump(a, lambda: bool(runs)), "the session did not start after the download"
    assert max(seen) == 100 and runs[0]["model"] == "medium"
    assert not a.dl_bar.winfo_ismapped()


def test_failed_download_shows_one_error_line_and_does_not_start(gui, monkeypatch):
    a, app, mdir, runs = gui

    def fake_download(model, put, *args, **kw):
        put(("dl_line", "fetch 20%"))
        put(("dl_done", model, 1))
        return 1

    monkeypatch.setattr(app, "run_download", fake_download)
    a.poll()
    a.toggle()
    assert _pump(a, lambda: a.downloading is None and a.start_dl is None)
    assert runs == [] and "failed" in a.v_status.get() and "\n" not in a.v_status.get()
    assert not a.dl_bar.winfo_ismapped()


def test_missing_pack_download_shows_progress_bar_too(gui, monkeypatch):
    a, app, mdir, runs = gui
    (mdir / "whisper-medium").mkdir()  # the model is there; only the language pack is missing
    seen = []
    state = {"missing": True}

    def fake_pack(pack, put, *args, **kw):
        for pct in (20, 100):
            put(("pack_line", f"pack {pct}%"))
        state["missing"] = False
        put(("pack_done", pack, 0))
        return 0

    monkeypatch.setattr(app.pack_on_demand, "first_missing", lambda *a, **k: ("ja", 1100) if state["missing"] else None)
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(app, "run_pack_download", fake_pack)
    orig = a._dl_event
    a._dl_event = lambda ev: (orig(ev), seen.append(a.v_dl_pct.get()))
    a.poll()
    a.toggle()
    assert a.downloading == "ja"
    assert _pump(a, lambda: bool(runs)), "the session did not start after the pack download"
    assert max(seen) == 100 and not a.dl_bar.winfo_ismapped()
