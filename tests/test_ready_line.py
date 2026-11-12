from tinglongwu import cli


class _Tty:
    def __init__(self, tty):
        self.tty, self.buf = tty, []

    def isatty(self):
        return self.tty

    def write(self, s):
        self.buf.append(s)

    def flush(self):
        pass


def _run(monkeypatch, capsys, tty, **kw):
    out = _Tty(tty)
    monkeypatch.setattr(cli.sys, "stdout", out)
    cli.announce_ready(None, **kw)
    return "".join(out.buf), capsys.readouterr().err


def test_interactive_tty_prints_ready_line_once_to_stdout(monkeypatch, capsys):
    out, err = _run(monkeypatch, capsys, tty=True)
    assert out == cli.READY_LINE + "\n"
    assert err == ""


def test_redirected_stdout_has_no_ready_line(monkeypatch, capsys):
    out, err = _run(monkeypatch, capsys, tty=False)
    assert out == ""
    assert err == cli.READY_LINE + "\n"


def test_transcript_only_keeps_stdout_clean_even_on_tty(monkeypatch, capsys):
    out, err = _run(monkeypatch, capsys, tty=True, transcript_only=True)
    assert out == ""
    assert cli.READY_LINE in err


def test_gui_status_callback_unchanged(capsys):
    seen = []
    cli.announce_ready(seen.append)
    assert seen == ["listening"]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
