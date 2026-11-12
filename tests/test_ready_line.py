from tinglongwu import cli


def test_cli_prints_ready_line_to_stdout(capsys):
    cli.announce_ready(None)
    out, err = capsys.readouterr()
    assert out == cli.READY_LINE + "\n"
    assert err == ""


def test_gui_status_callback_unchanged(capsys):
    seen = []
    cli.announce_ready(seen.append)
    assert seen == ["listening"]
    assert capsys.readouterr().out == ""
