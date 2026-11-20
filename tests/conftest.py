import os
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
