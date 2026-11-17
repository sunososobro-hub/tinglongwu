"""#1375: the GUI listen child gets --mt-pending, and a Hy-MT2-only background download does not block Start."""
from __future__ import annotations

from types import SimpleNamespace


import pytest

from tinglongwu.gui import core, pack_on_demand


def test_argv_has_mt_pending_for_auto_and_manual():
    assert "--mt-pending" in core.settings_to_argv({"quality": "auto", "target": "zh-tw"})
    assert "--mt-pending" in core.settings_to_argv({"quality": "manual", "engine": "hymt", "target": "zh-tw"})


def test_cli_parser_accepts_the_flag_from_gui_argv():
    from tinglongwu.cli import parse_args

    a = parse_args(core.settings_to_argv({"quality": "auto", "target": "zh-tw"}))
    assert a.mt_pending is True


def test_only_translation_model_packs():
    assert pack_on_demand.only_translation_model("ja")
    assert pack_on_demand.only_translation_model("ko")
    assert not pack_on_demand.only_translation_model("ru")  # also needs the Piper voice
    assert not pack_on_demand.only_translation_model("nope")


def _offer(monkeypatch, need, bg):
    pytest.importorskip("tkinter")
    from tinglongwu.gui import app as gui_app

    monkeypatch.setattr(pack_on_demand, "first_missing", lambda *a, **k: need)
    fake = SimpleNamespace(bg_dl=bg, downloading=None)
    return gui_app.App._offer_pack(fake, {"lang": "ja", "target": "zh"}, "listen")


def test_start_not_blocked_while_only_hymt_downloads(monkeypatch):
    bg = SimpleNamespace(active={"hymt2-1.8b"}, errors=[])
    assert _offer(monkeypatch, ("ja", 1100), bg) is False


def test_start_still_blocked_when_pack_needs_more(monkeypatch):
    bg = SimpleNamespace(active={"hymt2-1.8b"}, errors=[])
    assert _offer(monkeypatch, ("ru", 1160), bg) is True


def test_pending_or_none_defers_the_gui_manual_engine_hymt(tmp_path):
    from tinglongwu.cli import pending_or_none

    tr = pending_or_none(tmp_path, "hymt", lambda: None, warn=lambda m: None)  # empty models dir: Hy-MT2 not complete
    assert tr is not None
    assert pending_or_none(tmp_path, "ollama", lambda: None) is None  # other engines never wait for the download
