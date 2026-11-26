"""#1917 part 4: the GPU-offer and auto-update lines of the main window live in their own modules.

`App` inherits `GpuOfferMixin` (gui/app_gpu.py) and `UpdateMixin` (gui/app_update.py), so callers and tests keep
using `App._gpu_event`, `App._check_update`, ... and `app.UPDATE_LINE_WRAP_PX` / `app._mtime` unchanged.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("tkinter")

from tinglongwu import __version__  # noqa: E402
from tinglongwu.gui import app, app_gpu, app_update  # noqa: E402
from tinglongwu.i18n import t  # noqa: E402

GPU = ("_build_gpu_offer", "_gpu_detect", "_vk_detect", "_gpu_show", "_gpu_hide", "_gpu_offer_show", "_gpu_never",
       "_vk_install", "_gpu_auto_cancel", "_gpu_install", "_gpu_event")
UPD = ("_build_update_line", "_upd_show", "_upd_relabel", "_update_later", "_apply_pending_update",
       "_auto_update_check", "_check_update", "_update_found", "_update_yes", "_download_update", "_update_event",
       "_apply_update")


@pytest.mark.parametrize("mixin,names", [(app_gpu.GpuOfferMixin, GPU), (app_update.UpdateMixin, UPD)])
def test_app_gets_each_moved_method_from_its_mixin(mixin, names):
    assert issubclass(app.App, mixin)
    for n in names:
        assert n in vars(mixin), n
        assert n not in vars(app.App), n  # not shadowed by a stale copy left in app.py
        assert getattr(app.App, n) is getattr(mixin, n)


def test_app_module_still_exports_the_moved_names():
    assert app.UPDATE_LINE_WRAP_PX == 200 and app.UPDATE_QUIT_DELAY_MS == 1500
    assert app.UPDATE_LINE_WRAP_PX is app_update.UPDATE_LINE_WRAP_PX and app._mtime is app_gpu._mtime


def test_mtime_identifies_the_pack_folder(tmp_path):
    assert app._mtime(None) is None
    assert app._mtime(tmp_path / "missing") is None
    assert app._mtime(tmp_path) == tmp_path.stat().st_mtime_ns


def test_update_event_runs_from_the_mixin():
    shown = []
    stub = types.SimpleNamespace(upd_busy=True, upd_ver="9.9.9", _status_text=shown.append)
    assert app.App._update_event(stub, ("upd_none", True)) is True
    assert stub.upd_busy is False and shown == [t("gui_upd_none", ver=__version__)]
    assert app.App._update_event(stub, ("gpu_state", "x")) is False  # not an update event


def test_gpu_event_keeps_the_last_lines_of_the_pack_log():
    stub = types.SimpleNamespace(gpu_lines=[str(i) for i in range(25)])
    app.App._gpu_event(stub, ("gpu_pack_line", "new"))
    assert stub.gpu_lines == [str(i) for i in range(5, 25)] + ["new"]
