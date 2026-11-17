"""#1379: meeting notes from one switch: the #1378 default model downloads through the pack download child, the
switch alone saves the transcript, notes are written in the user's language, and they never cost a subtitle line."""
import json
import threading
import time

import pytest

from tinglongwu import topics
from tinglongwu.gui import core
from tinglongwu.gui.topics_panel import panel_hint
from tinglongwu.sink import JsonlSaveSink, Result

SUMMARY = "TITLE: Budget\n- point a\n- point b\nACTION me: send it\n"


def _install_default(mdir, pinned=True, file=True):
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "manifest.json").write_text(json.dumps({core.TOPICS_KEY: {"repo": "r"}} if pinned else {}), "utf-8")
    f = mdir / core.TOPICS_KEY / core.TOPICS_FILE
    if file:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"GGUF")
    return f


# ------------------------------------------------------------------ default model, settings -> argv
def test_default_model_counts_only_when_pinned_and_on_disk(tmp_path):
    assert core.default_topics_model(tmp_path / "none") == ""
    assert core.default_topics_model(tmp_path) == ""  # no manifest
    _install_default(tmp_path / "a", pinned=False)
    assert core.default_topics_model(tmp_path / "a") == ""  # file without a pin (half-finished download)
    _install_default(tmp_path / "b", file=False)
    assert core.default_topics_model(tmp_path / "b") == ""
    f = _install_default(tmp_path / "c")
    assert core.default_topics_model(tmp_path / "c") == str(f)


def test_pin_names_the_same_key_and_file(tmp_path):
    from pathlib import Path

    pins = json.loads((Path(core.__file__).parents[2] / "tinglongwu_setup" / "manifest.json").read_text("utf-8"))
    assert core.TOPICS_FILE in pins["models"][core.TOPICS_KEY]["files"]
    from tinglongwu_setup import downloader

    assert downloader.TOPICS_DIR == core.TOPICS_KEY and downloader.TOPICS_GGUF[1] == core.TOPICS_FILE
    assert downloader.TOPICS_GGUF[0] == pins["models"][core.TOPICS_KEY]["repo"]


def test_switch_alone_saves_the_transcript_and_uses_the_downloaded_model(tmp_path, monkeypatch):
    f = _install_default(tmp_path / "models")
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path / "models"))
    s = dict(core.DEFAULTS, save=False, topics=True, topics_model="")
    argv = core.settings_to_argv(s)
    assert "--save" in argv and argv[argv.index("--topics") + 1] == str(f)
    assert argv.count("--save") == 1
    s_saved = dict(s, save=True, save_path=str(tmp_path / "m.jsonl"))
    argv = core.settings_to_argv(s_saved)
    assert argv.count("--save") == 1 and argv[argv.index("--save") + 1] == str(tmp_path / "m.jsonl")
    off = core.settings_to_argv(dict(s, topics=False))
    assert "--topics" not in off and "--save" not in off  # notes off and save off: nothing is written
    own = tmp_path / "mine.gguf"
    own.write_bytes(b"GGUF")
    argv = core.settings_to_argv(dict(s, topics_model=str(own)))
    assert argv[argv.index("--topics") + 1] == str(own)  # a picked GGUF wins over the default


def test_no_model_means_no_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path / "empty"))
    argv = core.settings_to_argv(dict(core.DEFAULTS, save=False, topics=True, topics_model=""))
    assert "--topics" not in argv and "--save" not in argv


def test_unset_choice_follows_gpu_with_the_default_model(tmp_path):
    _install_default(tmp_path)
    s = {"topics": None, "topics_model": ""}
    assert core.topics_on(s, has_cuda=True, mdir=tmp_path) is True
    assert core.topics_on(s, has_cuda=False, mdir=tmp_path) is False
    assert core.topics_on(dict(s, topics=True), has_cuda=False, mdir=tmp_path) is True  # opt-in on CPU


# ------------------------------------------------------------------ download child
def test_download_child_fetches_the_pinned_notes_model(tmp_path, monkeypatch):
    cmd = core.topics_download_cmd(tmp_path, bundled=False)
    assert "--topics" in cmd and "--whisper" not in cmd
    assert cmd[cmd.index("--models-dir") + 1] == str(tmp_path)
    from tinglongwu_setup import downloader

    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):  # downloader.main() pops these; restore them after
        monkeypatch.setenv(k, "1")
    got = []
    monkeypatch.setattr(downloader, "get_gguf", lambda man, mdir, name, repo, fname, extras=(): got.append(
        (name, repo, fname)) or man.update({name: {"repo": repo}}))
    assert downloader.main(cmd[cmd.index("--topics"):]) == 0
    assert got == [(core.TOPICS_KEY, *downloader.TOPICS_GGUF)]
    assert core.TOPICS_KEY in json.loads((tmp_path / "manifest.json").read_text("utf-8"))


def test_download_child_can_be_cancelled():
    events, started = [], []

    class Proc:
        def __init__(self):
            self.stdout = iter(["Qwen3-4B-Instruct-2507-Q4_K_M.gguf:  12%|#  | 300M/2.50G\n"])
            self.killed = False

        def wait(self):
            return -15 if self.killed else 0

        def terminate(self):
            self.killed = True

    def on_start(p):
        started.append(p)
        p.terminate()  # what the panel's Cancel button does

    rc = core.run_pack_download("topics", events.append, popen=lambda *a, **k: Proc(), cmd=["x"], on_start=on_start)
    assert len(started) == 1 and rc == -15
    assert events[-1] == ("pack_done", "topics", -15)
    sd = core.StartDownload("notes")
    for ev in events:
        sd.feed(ev)
    assert sd.done and not sd.ok and sd.percent == 12


# ------------------------------------------------------------------ language
def test_prompt_asks_for_the_users_language():
    lines = [topics.Line(0, "them", "hello")]
    assert "Write in Traditional Chinese," in topics.build_prompt(lines, "zh-tw")
    assert "Write in Japanese," in topics.build_prompt(lines, "ja")
    assert "same language as the transcript" in topics.build_prompt(lines)  # bake-off prompt unchanged
    assert "same language as the transcript" in topics.build_prompt(lines, "xx")


def test_notes_use_the_users_own_words_and_the_translation_of_the_other_side(tmp_path):
    saver = JsonlSaveSink(None, tmp_path / "t.jsonl")
    seen = []
    notes = topics.attach(saver, "unused.gguf", summarize=lambda p: seen.append(p) or SUMMARY, lang="zh-tw")
    saver.record(Result("ru", "privet", None, "你好", tgt="zh"))
    saver.record(Result("zh", "早安", None, "dobroe utro", tgt="ru", side="me"))
    notes.close(timeout=10)
    assert "[them] 你好" in seen[0] and "[me] 早安" in seen[0] and "dobroe utro" not in seen[0]
    assert "Write in Traditional Chinese" in seen[0]


def test_cli_notes_language_is_the_target_language():
    from types import SimpleNamespace

    from tinglongwu import cli

    assert cli.notes_lang(SimpleNamespace(to="zh", zh_variant="cn")) == "zh-cn"
    assert cli.notes_lang(SimpleNamespace(to="zh", zh_variant="tw")) == "zh-tw"
    assert cli.notes_lang(SimpleNamespace(to="ja", zh_variant="tw")) == "ja"
    assert cli.notes_lang(cli.parse_args(["--to", "en"])) == "en"
    assert topics.lang_name(cli.notes_lang(cli.parse_args([]))) is not None


# ------------------------------------------------------------------ panel hint
def test_panel_hint_says_why_notes_are_off_on_a_cpu_only_pc():
    def no_probe():
        raise AssertionError("GPU probe not needed")

    kw = dict(downloading=False, has_model=True, has_entries=False, choice=None)
    assert panel_hint(**kw, has_cuda=lambda: False) == "gui_topics_cpu_off"
    assert panel_hint(**kw, has_cuda=lambda: True) == "gui_topics_empty"
    assert panel_hint(**dict(kw, choice=True), has_cuda=no_probe) == "gui_topics_empty"  # opted in on CPU
    assert panel_hint(**dict(kw, has_model=False), has_cuda=no_probe) == "gui_topics_no_model"
    assert panel_hint(**dict(kw, downloading=True), has_cuda=no_probe) == "gui_topics_downloading"
    assert panel_hint(**dict(kw, has_entries=True), has_cuda=no_probe) == ""


def test_every_ui_language_has_the_new_lines():
    from tinglongwu.i18n import MESSAGES

    for lang, msgs in MESSAGES.items():
        for k in ("gui_topics_switch", "gui_topics_lang", "gui_topics_cpu_off", "gui_topics_downloading",
                  "gui_topics_cancel"):
            assert msgs.get(k), (lang, k)
        assert "{lang}" in msgs["gui_topics_lang"] and "{mb}" in msgs["gui_topics_downloading"]


# ------------------------------------------------------------------ replay A/B: notes never cost a subtitle line
def _replay(tmp_path, name, notes_on):
    """300 final lines fed like a 10-minute meeting with short pauses (topics close every ~14 lines) into the save
    sink; the summarizer takes 0.3 s per topic (slower than the lines). Returns (lines saved, worst record() ms)."""
    saver = JsonlSaveSink(None, tmp_path / f"{name}.jsonl", clock=iter(range(10 ** 6)).__next__)
    notes = None
    if notes_on:
        notes = topics.attach(saver, "unused.gguf", summarize=lambda p: time.sleep(0.3) or SUMMARY, lang="en")
        t = [0.0]

        def on_line(rec, inner=saver.on_line):  # pause every 14 lines so the segmenter closes topics during the run
            t[0] += 12.0 if rec.get("n", 0) % 14 == 13 else 5.0
            inner(dict(rec, t=t[0]))

        saver.on_line = on_line
    worst = 0.0
    for i in range(300):
        t0 = time.perf_counter()
        saver.record(Result("ru", f"line {i}", None, f"строка {i}", tgt="en"))
        worst = max(worst, time.perf_counter() - t0)
        if notes_on and i % 14 == 13:
            time.sleep(0.01)
    saver.f.close()
    if notes is not None:
        assert notes.worker.pending() > 0 or notes.topics  # the summarizer really was busy during the replay
        notes.close(timeout=30)
    saved = (tmp_path / f"{name}.jsonl").read_text("utf-8").splitlines()
    return len(saved), worst * 1000


def test_replay_ab_notes_add_no_dropped_lines(tmp_path):
    off_lines, _ = _replay(tmp_path, "off", False)
    on_lines, worst_ms = _replay(tmp_path, "on", True)
    assert off_lines == on_lines == 300  # 0 added drops with notes on
    assert worst_ms < 50, f"record() took {worst_ms:.0f} ms while the notes model was busy"
    assert (tmp_path / "on.topics.md").read_text("utf-8").count("## ") >= 2


# ------------------------------------------------------------------ the real Tk window (xvfb in CI)
def _tk_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app as appmod

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("TLWU_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    return appmod, appmod.App()


def _drain(a, until, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not until():
        a.poll()
        a.root.update()
        time.sleep(0.02)


def test_gui_switch_on_downloads_the_model_then_stays_on(tmp_path, monkeypatch):
    appmod, a = _tk_app(tmp_path, monkeypatch)
    try:
        asked, cmds = [], []
        monkeypatch.setattr(appmod.messagebox, "askyesno", lambda title, text, **k: asked.append(text) or True)

        def fake_download(pack, put, cmd=None, on_start=None, **k):
            cmds.append(cmd)
            put(("pack_line", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf:  50%|##  | 1.25G/2.50G"))
            _install_default(tmp_path / "models")
            put(("pack_done", pack, 0))
            return 0

        monkeypatch.setattr(appmod, "run_pack_download", fake_download)
        a.settings.update(topics=None, topics_model="", target="zh-tw")
        a._topics_var().set(True)
        a._topics_toggled()
        assert asked and "2500 MB" in asked[0]
        _drain(a, lambda: a.downloading is None)
        assert "--topics" in cmds[0]
        assert a.settings["topics"] is True and a.v_topics.get() is True
        assert core.load_settings(gpu=lambda: False)["topics"] is True  # saved
        assert a.topics_win.winfo_viewable()
        assert "Traditional Chinese" in a.topics_lang.cget("text")
        assert a.topics_hint.cget("text") == appmod.t("gui_topics_empty")
        assert "--topics" in core.settings_to_argv(a.settings)
    finally:
        a.root.destroy()


def test_gui_not_now_or_cancel_turns_the_switch_back_off(tmp_path, monkeypatch):
    appmod, a = _tk_app(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(appmod.messagebox, "askyesno", lambda *x, **k: False)
        a.settings.update(topics=None, topics_model="")
        a._topics_var().set(True)
        a._topics_toggled()
        assert a.settings["topics"] is False and a.v_topics.get() is False and a.downloading is None

        monkeypatch.setattr(appmod.messagebox, "askyesno", lambda *x, **k: True)
        gate = threading.Event()

        class Proc:
            rc = None

            def poll(self):
                return self.rc

            def terminate(self):
                self.rc = -15
                gate.set()

        def fake_download(pack, put, cmd=None, on_start=None, **k):
            p = Proc()
            on_start(p)
            gate.wait(10)
            put(("pack_done", pack, p.rc))
            return p.rc

        monkeypatch.setattr(appmod, "run_pack_download", fake_download)
        a._topics_var().set(True)
        a._topics_toggled()
        _drain(a, lambda: getattr(a, "topics_proc", None) is not None)
        a.render_topics()
        assert a.topics_cancel.winfo_ismapped() or a.topics_cancel.winfo_manager() == "pack"
        assert "MB" in a.topics_hint.cget("text")  # downloading line
        a._cancel_topics_download()
        _drain(a, lambda: a.downloading is None)
        assert a.settings["topics"] is False and a.v_topics.get() is False
        assert a.topics_cancel.winfo_manager() == ""
    finally:
        a.root.destroy()


def test_gui_cpu_only_pc_explains_why_notes_are_off(tmp_path, monkeypatch):
    appmod, a = _tk_app(tmp_path, monkeypatch)
    try:
        _install_default(tmp_path / "models")
        monkeypatch.setattr(core, "_gpu_usable", lambda: False)
        a.settings.update(topics=None, topics_model="")
        a.render_topics(show=True)
        assert a.topics_hint.cget("text") == appmod.t("gui_topics_cpu_off")
        assert a.v_topics.get() is False
        monkeypatch.setattr(core, "_gpu_usable", lambda: True)
        a._sync_topics_var()
        assert a.v_topics.get() is True  # with a GPU the default is on
    finally:
        a.root.destroy()
