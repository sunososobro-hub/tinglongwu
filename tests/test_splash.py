"""CLI start-up splash: frame data loads; it is skipped for non-TTY, NO_COLOR, --no-splash, CI and non-truecolor."""
import io

from tinglongwu import splash

TTY_ENV = {"COLORTERM": "truecolor", "TERM": "xterm-256color"}


class FakeTTY(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 1


def test_frames_load():
    d = splash.load_frames()
    assert d["w"] > 0 and d["h"] > 0 and len(d["frames"]) >= 10
    assert 1.0 <= len(d["frames"]) / d["fps"] <= 2.0  # about 1.5 s
    for fr in d["frames"]:
        assert fr.count("\n") + 1 == d["h"] and "\u2580" in fr and "\x1b[38;2;" in fr


def _ok(monkeypatch, **kw):
    monkeypatch.delitem(__import__("sys").modules, "pytest", raising=False)  # should_show() skips under pytest itself
    monkeypatch.setattr("sys.stdin", FakeTTY())
    return splash.should_show(stream=FakeTTY(), size=(80, 30), **kw)


def test_shown_on_truecolor_tty(monkeypatch):
    assert _ok(monkeypatch, env=dict(TTY_ENV)) is True


def test_skipped_when_not_a_tty(monkeypatch):
    monkeypatch.delitem(__import__("sys").modules, "pytest", raising=False)
    assert splash.should_show(stream=io.StringIO(), env=dict(TTY_ENV), size=(80, 30)) is False


def test_skipped_with_no_color_flag_ci_and_no_truecolor(monkeypatch):
    assert _ok(monkeypatch, env={**TTY_ENV, "NO_COLOR": "1"}) is False
    assert _ok(monkeypatch, env=dict(TTY_ENV), no_splash=True) is False
    assert _ok(monkeypatch, env={**TTY_ENV, "CI": "true"}) is False
    assert _ok(monkeypatch, env={"TERM": "xterm"}) is False
    assert _ok(monkeypatch, env={**TTY_ENV, "PYTEST_CURRENT_TEST": "x"}) is False
    assert _ok(monkeypatch, env={**TTY_ENV, "TLWU_NO_SPLASH": "1"}) is False


def test_skipped_on_small_terminal(monkeypatch):
    monkeypatch.delitem(__import__("sys").modules, "pytest", raising=False)
    monkeypatch.setattr("sys.stdin", FakeTTY())
    assert splash.should_show(stream=FakeTTY(), env=dict(TTY_ENV), size=(40, 10)) is False


def test_play_writes_nothing_when_skipped():
    out = io.StringIO()
    assert splash.play(no_splash=True, stream=out) is False and out.getvalue() == ""


def test_play_animates_and_cleans_up(monkeypatch):
    monkeypatch.setattr(splash, "should_show", lambda *a, **k: True)
    monkeypatch.setattr(splash, "enable_vt", lambda: True)
    monkeypatch.setattr(splash.time, "sleep", lambda s: None)
    out = FakeTTY()
    assert splash.play(stream=out) is True
    text = out.getvalue()
    assert text.startswith("\x1b[?25l") and text.endswith("\x1b[?25h") and "\x1b[J" in text


def test_cli_accepts_no_splash():
    from tinglongwu.cli import parse_args

    assert parse_args(["--no-splash"]).no_splash is True and parse_args([]).no_splash is False
