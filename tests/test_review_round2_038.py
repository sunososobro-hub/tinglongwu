"""0.3.8 review round 2: bundled detection, atomic pylib, stable speaker ids, slow-load timing, misc."""
import hashlib
import io
import sys
import zipfile

import pytest

from tinglongwu import speaker_pylib
from tinglongwu.gui import speaker_pack as sp
from tinglongwu.speaker_embed import LiveSpeakers
from tinglongwu.speakers import SpeakerClusterer


def _whl(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)


# 1. packaged-build detection
def test_is_frozen_uses_is_bundled(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("TLWU_BUNDLED", "1")
    assert sp.is_frozen() is True
    monkeypatch.delenv("TLWU_BUNDLED")
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\tlwu\tlwu.exe")
    assert sp.is_frozen() is True
    monkeypatch.setattr(sys, "executable", r"C:\py\python.exe")
    assert sp.is_frozen() is False


def test_bundled_without_sys_frozen_takes_wheel_path_not_pip(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("TLWU_BUNDLED", "1")
    seen = []
    sp.run_download(lambda e: None, tmp_path, "req.txt", runner=lambda *a, **k: seen.append(k["cmd"]) or 0,
                    pkg_ok=lambda: False, model_ok=lambda d: True)
    assert len(seen) == 1 and "--speaker-pylib" in seen[0] and "pip" not in seen[0]


# 2. atomic pylib + validity marker
def _pin(src, f):
    return (f, (src / f).stat().st_size, hashlib.sha256((src / f).read_bytes()).hexdigest(), "http://x/")


def test_pylib_extract_is_atomic_and_replaces_half_tree(tmp_path):
    from tinglongwu_setup import downloader as dl

    src = tmp_path / "src"
    src.mkdir()
    _whl(src / "a.whl", {"sherpa_onnx/__init__.py": "x=1"})
    _whl(src / "c.whl", {"core/b.txt": "c"})
    md = tmp_path / "models"
    half = md / "speaker" / "pylib"
    (half / "stale").mkdir(parents=True)
    (half / "stale" / "junk.dll").write_text("x")  # half-extracted leftovers, no marker
    opener = lambda url, timeout: io.BytesIO((src / url.rsplit("/", 1)[1]).read_bytes())  # noqa: E731
    dl.get_speaker_pylib(md, opener=opener, tag="cp312", wheels={"cp312": _pin(src, "a.whl")}, core=_pin(src, "c.whl"))
    assert not (half / "stale").exists() and (half / ".pins").is_file() and (half / "sherpa_onnx/__init__.py").is_file()
    assert not (md / "speaker" / "pylib.new").exists()


def test_pylib_extract_failure_leaves_no_pylib(tmp_path, monkeypatch):
    from tinglongwu_setup import downloader as dl

    src = tmp_path / "src"
    src.mkdir()
    _whl(src / "a.whl", {"x/__init__.py": "1"})
    _whl(src / "c.whl", {"y": "1"})
    md = tmp_path / "models"

    def boom(self, d):
        raise OSError("disk full")

    monkeypatch.setattr(dl.zipfile.ZipFile, "extractall", boom)
    opener = lambda url, timeout: io.BytesIO((src / url.rsplit("/", 1)[1]).read_bytes())  # noqa: E731
    with pytest.raises(OSError):
        dl.get_speaker_pylib(md, opener=opener, tag="cp312", wheels={"cp312": _pin(src, "a.whl")}, core=_pin(src, "c.whl"))
    assert not (md / "speaker" / "pylib").exists()


def test_bundled_pkg_installed_requires_valid_pins(tmp_path):
    d = tmp_path / "speaker" / "pylib"
    d.mkdir(parents=True)
    assert sp.pkg_installed(tmp_path, bundled=True) is False  # folder without marker = half extracted
    (d / ".pins").write_text("", "utf-8")
    assert speaker_pylib.pylib_valid(tmp_path) is False  # empty marker = interrupted write
    (d / ".pins").write_text("sherpa_onnx-2.0.1 core-2.0.1", "utf-8")
    assert speaker_pylib.pylib_valid(tmp_path) is True  # pin mismatch is setup's job (it re-extracts)


def test_module_present_finds_package_without_importing(tmp_path, monkeypatch):
    (tmp_path / "fakepkg_1070").mkdir()
    (tmp_path / "fakepkg_1070" / "__init__.py").write_text("raise RuntimeError('must not be imported')")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert speaker_pylib.module_present("fakepkg_1070") is True
    assert speaker_pylib.module_present("no_such_pkg_1070") is False


def test_broken_import_counts_as_missing_for_repair(tmp_path, monkeypatch):
    (tmp_path / "speaker").mkdir()
    (tmp_path / "speaker" / sp.MODEL_NAME).write_text("m")
    monkeypatch.setattr(sp, "pkg_installed", lambda mdir=None, bundled=None: True)
    monkeypatch.setattr(sp, "import_ok", lambda mdir: False)
    assert sp.missing(tmp_path) == (True, False)
    asked = []
    r = sp.on_toggle(True, tmp_path, lambda *a: asked.append(a) or True, lambda: None)
    assert r == "downloading" and asked  # offered a re-download instead of silently "on"


# 3. one clusterer for the session
def test_shared_clusterer_keeps_numbering_across_labelers():
    c = SpeakerClusterer()
    a = c.assign([1.0, 0.0], 2.0)
    b = c.assign([0.0, 1.0], 2.0)
    assert a != b
    # a second labeler reusing the same clusterer: same voice -> same label; a new voice continues the numbering
    assert c.assign([1.0, 0.0], 2.0) == a
    third = c.assign([-1.0, -1.0], 2.0)
    assert third not in (a, b) and c.count == 3


def test_make_labeler_passes_clusterer(tmp_path):
    got = {}
    c = SpeakerClusterer()
    sp.make_labeler({"speaker_id": True}, tmp_path, factory=lambda m, on_ready=None, clusterer=None: got.update(c=clusterer),
                    clusterer=c)
    assert got["c"] is c


# 5. embedded requirements
def test_embedded_requirements_equal_file():
    from pathlib import Path

    from tinglongwu.speaker_reqs import REQUIREMENTS

    f = Path(__file__).resolve().parents[1] / "requirements/requirements-speaker.txt"
    assert REQUIREMENTS == f.read_text("utf-8")
    assert sp.requirements_file().read_text("utf-8") == REQUIREMENTS


def test_pip_failure_reports_real_reason(tmp_path):
    evs = []

    def runner(pack, put, **k):
        put(("pack_line", "ERROR: No matching distribution found for sherpa-onnx"))
        return 1

    rc = sp.run_download(evs.append, tmp_path, "r.txt", frozen=False, runner=runner, pkg_ok=lambda: False,
                         model_ok=lambda d: True)
    assert rc == 1 and ("pack_fail_reason", "speaker", "ERROR: No matching distribution found for sherpa-onnx") in evs


# minor: activate idempotent, LiveSpeakers drops closed labelers
def test_activate_adds_dll_dirs_once(tmp_path, monkeypatch):
    d = tmp_path / "speaker" / "pylib" / "lib"
    d.mkdir(parents=True)
    (d / "a.dll").write_text("x")
    calls = []
    monkeypatch.setattr(speaker_pylib.os, "add_dll_directory", lambda p: calls.append(p) or object(), raising=False)
    monkeypatch.setattr(speaker_pylib, "_dll_handles", {})
    monkeypatch.setattr(sys, "path", list(sys.path))
    speaker_pylib.activate(tmp_path)
    speaker_pylib.activate(tmp_path)
    assert len(calls) == 1


class _Lab:
    def __init__(self):
        self._res, self.closed = {}, False

    def close(self):
        self.closed = True


def test_live_speakers_drops_labelers_but_keeps_results():
    a, b = _Lab(), _Lab()
    a._res["spk#1"] = "S1"
    live = LiveSpeakers(a)
    live.set(b)
    assert a.closed and a not in live._all
    assert live.result("spk#1") == "S1"
    live.close()
    assert b.closed and live._all == [] and live.result("spk#1") == "S1"


# 6. slow-load line timing (Tk after timer)
class _Var:
    def __init__(self, v=""):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


def _bare_app():
    from tinglongwu.gui import app as appmod

    cls = [v for v in vars(appmod).values() if isinstance(v, type) and hasattr(v, "_slow_load_due")][0]
    o = object.__new__(cls)
    o.v_status, o.errored, o._slow_after, o._slow_shown, o.status_key = _Var("Loading speech model"), False, None, False, None
    return o


def test_slow_line_appears_while_loading_after_10s_and_not_before():
    import time as _t

    o = _bare_app()
    o.load_t0 = _t.monotonic() - 3
    o._slow_load_due()
    assert not o._slow_shown and "\n" not in o.v_status.get()
    o.load_t0 = _t.monotonic() - 11
    o._slow_load_due()
    assert o._slow_shown and o.v_status.get().startswith("Loading speech model\n")


def test_slow_line_cleared_on_ready_and_never_after():
    o = _bare_app()
    cancelled = []
    o.root = type("R", (), {"after_cancel": lambda self, i: cancelled.append(i)})()
    o._slow_after, o._slow_shown = "id1", True
    o._slow_clear()
    assert cancelled == ["id1"] and not o._slow_shown
    o.v_status.set("ready")
    o.load_t0 = None
    o._slow_load_due()  # timer fired after ready: nothing is added
    assert o.v_status.get() == "ready"
