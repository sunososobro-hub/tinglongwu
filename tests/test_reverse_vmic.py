import subprocess
from types import SimpleNamespace

import pytest

from tinglongwu.reverse import vmic
from tinglongwu.reverse.live import check_mic_feedback
from tinglongwu.reverse.proto import build_translator


def test_load_commands_exact():
    c = vmic.load_commands()
    assert c[0] == ["pactl", "load-module", "module-null-sink", "sink_name=tlwu_sink",
                    "sink_properties=device.description=tlwu_sink"]
    assert c[1] == ["pactl", "load-module", "module-remap-source", "master=tlwu_sink.monitor",
                    "source_name=tlwu_mic", "source_properties=device.description=tlwu_mic"]
    assert vmic.unload_command("7") == ["pactl", "unload-module", "7"]


def _fake(calls, fail_on=None):
    def run(cmd):
        calls.append(cmd)
        if fail_on and fail_on in cmd[2]:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout=f"{40 + len(calls)}\n", stderr="")
    return run


def test_open_close_unloads_in_reverse_order():
    calls = []
    v = vmic.VirtualMic(run=_fake(calls)).open()
    assert v.ids == ["41", "42"]
    v.close()
    v.close()  # idempotent
    assert calls[2:] == [["pactl", "unload-module", "42"], ["pactl", "unload-module", "41"]]


def test_failure_rolls_back_and_raises():
    calls = []
    with pytest.raises(RuntimeError, match="boom"):
        vmic.VirtualMic(run=_fake(calls, fail_on="module-remap-source")).open()
    assert calls[-1] == ["pactl", "unload-module", "41"]


def test_missing_pactl():
    def run(cmd):
        raise FileNotFoundError
    with pytest.raises(RuntimeError, match="pactl not found"):
        vmic.VirtualMic(run=run).open()


def test_context_manager_and_platform(monkeypatch):
    calls = []
    with vmic.VirtualMic(run=_fake(calls)):
        pass
    assert len(calls) == 4
    monkeypatch.setattr(vmic.sys, "platform", "win32")
    assert not vmic.is_linux()
    monkeypatch.setattr(vmic.sys, "platform", "linux")
    assert vmic.is_linux()


def test_feedback_guard_refuses_virtual_mic():
    with pytest.raises(ValueError, match="feedback guard"):
        check_mic_feedback("tlwu_mic", "tlwu_sink")
    with pytest.raises(ValueError):
        check_mic_feedback("Monitor of tlwu_sink", None)


def test_build_translator_opus_on_cpu(monkeypatch, tmp_path):
    import tinglongwu.translate as tr
    loaded = []
    monkeypatch.setattr(tr.PivotTranslator, "preload", lambda self, s, t: loaded.append((s, t)))
    mt = build_translator("auto", "cpu", tmp_path, "qwen3-4b")
    assert isinstance(mt, tr.PivotTranslator) and mt.device == "cpu"
    assert loaded == [("zh", "ru"), ("ru", "zh")] and mt.history == []
