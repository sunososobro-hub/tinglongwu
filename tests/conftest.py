import gc
import os
import re
import signal
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for _layer in ("system", "ui", "core"):  # src/ layer layout (#1664); core last-inserted = first on the path
    sys.path.insert(0, str(ROOT / "src" / _layer))


@pytest.fixture(autouse=True)
def _ui_lang_en(monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.fixture(autouse=True)
def _no_cpu_mode_threads():
    """#1198: threads.set_cpu_mode is process-global (cli.apply_cpu_mode); never let one test's plan leak."""
    from tinglongwu import threads

    threads.set_cpu_mode(None)
    yield
    threads.set_cpu_mode(None)
    from tinglongwu import instances

    instances.release_all()  # #1254: no lock file held open from one test to the next


@pytest.fixture
def wav(tmp_path):
    import soundfile as sf

    p = tmp_path / "in.wav"
    t = np.arange(44100 * 3) / 44100
    sf.write(str(p), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 44100)
    return p


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path, monkeypatch):
    """Network-guard logs / config go to a temp dir, never the real user dirs."""
    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path / "_log"))
    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path / "_cfg"))
    # #1254: CPU-mode instance registry per test, never the real one of a tlwu running on this PC
    monkeypatch.setenv("TLWU_INSTANCE_DIR", str(tmp_path / "_instances"))
    # GUI settings live under %APPDATA% on Windows: never read the developer's real gui.json in tests
    monkeypatch.setenv("APPDATA", str(tmp_path / "_appdata"))
    monkeypatch.delenv("TLWU_ALLOW_NET", raising=False)
    # every test starts as a source install: tlwu_app.main.prepare_env() writes TLWU_BUNDLED=1 into os.environ (#768)
    monkeypatch.delenv("TLWU_BUNDLED", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)


@pytest.fixture(autouse=True)
def _restore_sigint():
    """gui.App() installs its own SIGINT handler for the whole process. Once real-Tk tests run (xvfb, #113) that
    handler outlived the GUI test, so a later `_thread.interrupt_main()` (test_speak_ctrlc_427) set App.sigint instead
    of raising KeyboardInterrupt and the test hung until --timeout. Every test starts and ends with the handler it found."""
    prev = signal.getsignal(signal.SIGINT)
    yield
    if signal.getsignal(signal.SIGINT) is not prev and prev is not None:
        signal.signal(signal.SIGINT, prev)


# -- parallel `test` job (#1857) ---------------------------------------------------------------------------------
# CI runs the suite with `pytest -n auto --dist loadgroup`. Files that build real Tk windows (or start a GUI child on
# the xvfb display) all go into ONE xdist group, so they run one after another on one worker, as in the old serial run:
# two workers driving Tk at once made a worker segfault in focus_force (test_send_gate_833, PR #1867).
# Every other file is its own group (= --dist loadfile), so module fixtures still run once per file.
REAL_TK_RE = re.compile(r"tkinter|\bTk\(|Toplevel|tinglongwu\.(?:gui|overlay|splash|face_to_face)"
                        r"|from tinglongwu import [^\n]*\b(?:gui|overlay|splash|face_to_face)\b|DISPLAY|xvfb")
REAL_TK_GROUP = "real-tk"
_tk_file_cache: dict = {}


def xdist_group_for(path, text=None) -> str:
    """The xdist group of a test file: REAL_TK_GROUP when its source touches Tk / the display, else the file itself."""
    p = Path(path)
    if text is None:
        if p not in _tk_file_cache:
            try:
                _tk_file_cache[p] = bool(REAL_TK_RE.search(p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                _tk_file_cache[p] = False
        hit = _tk_file_cache[p]
    else:
        hit = bool(REAL_TK_RE.search(text))
    return REAL_TK_GROUP if hit else p.name


@pytest.fixture(autouse=True)
def _collect_tk_garbage(request):
    """#1918 CI: free a real-Tk test's leftover tkinter objects (Variable / image cycles) on the main thread.

    Left to the cyclic GC they are finalized in whatever thread allocates next on this xdist worker; there
    `Variable.__del__` raises "main thread is not in main loop", killing that thread (test_pending_line_1532's
    pipeline thread) or aborting Tcl (test_readme_gif_drive_734 crashed worker gw0).
    """
    yield
    if "tkinter" in sys.modules and xdist_group_for(request.node.path) == REAL_TK_GROUP:
        gc.collect()


@pytest.hookimpl(tryfirst=True)  # before xdist's own hook, which appends the group to each nodeid
def pytest_collection_modifyitems(config, items):
    if not config.pluginmanager.hasplugin("xdist"):
        return
    for item in items:
        if item.get_closest_marker("xdist_group") is None:
            item.add_marker(pytest.mark.xdist_group(xdist_group_for(item.path)))


@pytest.fixture(autouse=True)
def _restore_offline_env():
    """#1857: tinglongwu_setup.downloader.main() pops HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE from os.environ (the
    download child needs the network). Tests that call it leaked that into every later test in the same process, so
    test_offline.py::test_env_set_on_import passed or failed by test order (it failed once the suite ran in parallel
    workers). Every test ends with the offline flags it started with."""
    from tinglongwu.offline import OFFLINE_ENV

    prev = {k: os.environ.get(k) for k in OFFLINE_ENV}
    yield
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# -- real-Tk GUI tests (#113) ------------------------------------------------------------------------------------
_TK_SKIP = ("no display", "no tkinter", "could not import 'tkinter'")
_DIALOGS = {"messagebox": ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askyesnocancel",
                           "askquestion", "askretrycancel"),
            "filedialog": ("askopenfilename", "asksaveasfilename", "askdirectory", "askopenfilenames",
                                          "askopenfile", "asksaveasfile")}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """CI runs the suite under xvfb with TLWU_REQUIRE_DISPLAY=1: a real-Tk test that skips for want of a display
    or tkinter would hide GUI regressions, so it is reported as a failure there."""
    out = yield
    rep = out.get_result()
    if rep.skipped and os.environ.get("TLWU_REQUIRE_DISPLAY") == "1" and isinstance(rep.longrepr, tuple):
        reason = str(rep.longrepr[2])
        if any(s in reason for s in _TK_SKIP):
            rep.outcome = "failed"
            rep.longrepr = f"TLWU_REQUIRE_DISPLAY=1 but a real-Tk test skipped: {reason}"


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """A tkinter messagebox / file dialog in a test waits for a click that never comes: under a real display (xvfb)
    the whole run hangs (#113: PR #736 ran 2 h+). Every dialog returns None (= No / Cancel) at once and the test
    fails; a test that expects a dialog monkeypatches that function itself."""
    if "tkinter" not in sys.modules:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            yield
            return
    import importlib

    seen = []
    for mod, names in _DIALOGS.items():
        m = importlib.import_module(f"tkinter.{mod}")
        for name in names:
            def fake(*args, _n=f"{mod}.{name}", **kw):
                seen.append((_n, args[:2]))
                return None
            monkeypatch.setattr(m, name, fake)
    yield
    if seen:
        pytest.fail(f"unexpected modal dialog(s) in a test (would block a real display): {seen}")


def real_tk_unsafe(platform, tk_version):
    """#1591: on macOS with Tk >= 9.0 (python 3.14 + Tk 9.1, Apple silicon) creating a real Tk root segfaults the whole
    pytest run. Root cause named here: Tk 9's Cocoa backend aborts when the root is created by the test setup."""
    return platform == "darwin" and float(tk_version) >= 9.0


def install_tk_guard(monkeypatch, platform=None):
    """Make Tk() / Toplevel() skip the test on macOS + Tk 9 (#1591). Returns True when the guard was installed."""
    try:
        import tkinter as tk
    except ImportError:
        return False
    if not real_tk_unsafe(platform or sys.platform, tk.TkVersion):
        return False

    def refuse(self, *a, **kw):
        pytest.skip(f"real Tk root is unsafe on macOS with Tk {tk.TkVersion} (segfault, #1591)")

    monkeypatch.setattr(tk.Tk, "__init__", refuse)
    monkeypatch.setattr(tk.Toplevel, "__init__", refuse)
    return True


@pytest.fixture(autouse=True)
def _skip_real_tk_on_mac_tk9(monkeypatch):
    """Skip (with a reason) any test that builds a real Tk / Toplevel on macOS + Tk 9, instead of crashing the run (#1591)."""
    install_tk_guard(monkeypatch)


@pytest.fixture(autouse=True)
def _tk_invisible(monkeypatch):
    """Owner 2026-11-15: running the tests must never flash windows on the desktop. Every Tk / Toplevel is created
    fully transparent (alpha 0, also when the app sets its own overlay alpha). Windows stay 'mapped' for Tk, so geometry / winfo_ismapped assertions keep working."""
    try:
        import tkinter as tk
    except ImportError:
        return
    real_init = {tk.Tk: tk.Tk.__init__, tk.Toplevel: tk.Toplevel.__init__}
    real_attrs = tk.Wm.wm_attributes

    def hush(self):
        try:
            real_attrs(self, "-alpha", 0.0)
        except tk.TclError:
            pass

    def make(cls):
        def init(self, *a, **kw):
            real_init[cls](self, *a, **kw)
            hush(self)
        return init

    def attrs(self, *a, **kw):
        if a and a[0] == "-alpha":
            if len(a) == 1:  # a query answers with what the app asked for
                return getattr(self, "_tl_alpha", real_attrs(self, "-alpha"))
            self._tl_alpha = float(a[1])
            a = (a[0], 0.0, *a[2:])
        if "alpha" in kw:
            self._tl_alpha = float(kw["alpha"])
            kw["alpha"] = 0.0
        return real_attrs(self, *a, **kw)

    monkeypatch.setattr(tk.Tk, "__init__", make(tk.Tk))
    monkeypatch.setattr(tk.Toplevel, "__init__", make(tk.Toplevel))
    monkeypatch.setattr(tk.Wm, "wm_attributes", attrs)
    monkeypatch.setattr(tk.Wm, "attributes", attrs)


@pytest.fixture(autouse=True)
def _fresh_echo_guard():  # #1070: the process-wide listen echo guard must not leak between tests
    from tinglongwu.reverse.echoguard import shared

    shared()._items.clear()
    yield
