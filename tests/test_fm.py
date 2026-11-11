import pytest

from tinglongwu import cli
from tinglongwu.fm import FmTranslator
from tinglongwu.qwen import choose_engine


class Runner:
    def __init__(self, out="你好", code=0, err=""):
        self.calls, self.out, self.code, self.err = [], out, code, err

    def __call__(self, cmd, stdin):
        self.calls.append((cmd, stdin))
        return self.code, self.out, self.err


def test_translate_command_shape_and_on_device_only():
    r = Runner("<think>x</think>hello")
    tr = FmTranslator(runner=r)
    assert tr.translate("hi there", "ru", "en").text == "hello"
    cmd, stdin = r.calls[-1]
    assert cmd[1:4] == ["respond", "--model", "system"] and "pcc" not in cmd
    assert "--no-stream" in cmd and "--greedy" in cmd and "--instructions" in cmd
    assert "hi there" in stdin


def test_history_used_as_context_but_not_for_drafts():
    r = Runner("hello")
    tr = FmTranslator(runner=r)
    tr.translate("privet", "ru", "en")
    tr.draft("kak dela", "ru", "en")
    assert len(tr.history) == 1
    tr.translate("poka", "ru", "en")
    assert "privet => " in r.calls[-1][1]


def test_same_language_and_empty_skip_process():
    r = Runner()
    tr = FmTranslator(runner=r)
    assert tr.translate("hi", "en", "en").text == "hi"
    assert tr.translate("  ", "en", "zh").text == "  "
    assert r.calls == []


def test_license_error_has_hint():
    with pytest.raises(RuntimeError, match="sudo fm license"):
        FmTranslator(runner=Runner(code=69, err="not agreed")).preload()


def test_missing_binary(monkeypatch):
    monkeypatch.setattr("tinglongwu.fm.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="macOS 27"):
        FmTranslator()


def test_engine_wiring(tmp_path, monkeypatch):
    assert choose_engine("fm", "cpu", tmp_path) == ("fm", "requested")
    monkeypatch.setattr("tinglongwu.fm.shutil.which", lambda _: "/usr/bin/fm")
    tr = cli.make_translator("fm", tmp_path, "tw", "cpu", say=lambda *a, **k: None)
    assert isinstance(tr, FmTranslator)
