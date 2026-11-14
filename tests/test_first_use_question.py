"""#680: one-time local "what do you mainly use TingLong for?" question. Core logic only (no Tk); nothing is sent."""
from __future__ import annotations

import socket

import pytest

from tinglongwu.gui import core
from tinglongwu.i18n import MESSAGES


def test_asked_once_then_never_again(tmp_path):
    p = tmp_path / "gui.json"
    s = core.load_settings(p)
    assert core.usage_should_ask(s)  # fresh install: ask after the first subtitle
    core.usage_answer(s, "meetings")
    core.save_settings(s, p)
    s2 = core.load_settings(p)
    assert not core.usage_should_ask(s2)
    assert s2["usage_use"] == "meetings"


def test_skip_is_remembered_without_an_answer(tmp_path):
    p = tmp_path / "gui.json"
    s = core.load_settings(p)
    core.usage_answer(s, None)
    core.save_settings(s, p)
    s2 = core.load_settings(p)
    assert not core.usage_should_ask(s2) and s2["usage_use"] == ""


def test_can_be_changed_later_and_unknown_choice_is_ignored():
    s = dict(core.DEFAULTS)
    core.usage_answer(s, "videos")
    core.usage_answer(s, "learning")  # settings menu: change it
    assert s["usage_use"] == "learning"
    core.usage_answer(s, "<script>")
    assert s["usage_use"] == "" and s["usage_asked"] is True


def test_prefill_only_when_checkbox_ticked_and_answered():
    s = dict(core.DEFAULTS, usage_use="calls", lang="en", target="zh-tw")
    assert core.usage_prefill(s, include=False) == {}
    assert core.usage_prefill(dict(core.DEFAULTS), include=True) == {}
    assert core.usage_prefill(s, include=True) == {"Mainly used for": "calls", "Language pair": "en -> zh-tw"}


def test_no_network_is_touched(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    p = tmp_path / "gui.json"
    s = core.load_settings(p)
    core.usage_answer(s, "other")
    assert core.save_settings(s, p)
    core.usage_prefill(s, True)


@pytest.mark.parametrize("lang", ["en", "ru", "zh-TW"])
def test_every_choice_has_a_label(lang):
    for c in core.USAGE_CHOICES:
        assert MESSAGES[lang]["gui_usage_" + c]
    assert MESSAGES[lang]["gui_usage_title"] and MESSAGES[lang]["gui_menu_usage"]


def test_skip_or_close_keeps_an_earlier_saved_answer():
    s = dict(core.DEFAULTS)
    core.usage_answer(s, "meetings")
    core.usage_answer(s, None)  # reopened from Settings, closed without Save
    assert s["usage_use"] == "meetings" and s["usage_asked"] is True
