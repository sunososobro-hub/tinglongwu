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


def _stub_run_session(monkeypatch, tmp_path, tty):
    """Run cli.run_session with every model/source/pipeline stubbed; return (stdout, stderr) text."""
    import io
    import sys
    import types

    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod

    class _Asr:
        device = "cpu"
        live = False

    class _Err(io.StringIO):
        pass

    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", lambda *a, **k: _Asr())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: object())
    monkeypatch.setattr(pipeline_mod, "run", lambda *a, **k: None)
    out, err = _Tty(tty), _Err()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    args = cli.parse_args(["--transcript-only", "--file", str(tmp_path / "in.wav"), "--model", "tiny",
                           "--models-dir", str(tmp_path)])
    cli.run_session(args)
    return "".join(out.buf), err.getvalue()


def test_run_session_transcript_only_keeps_ready_line_off_tty_stdout(monkeypatch, tmp_path):
    out, err = _stub_run_session(monkeypatch, tmp_path, tty=True)
    assert cli.READY_LINE not in out
    assert cli.READY_LINE in err
