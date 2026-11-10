import getpass
import os
from urllib.parse import parse_qs, urlparse

from tinglongwu import cli, report


def test_url_encoding_and_length_cap():
    logs = [f"line {i} " + "é中 " * 40 for i in range(60)]
    url = report.build_url(info={"Engine": "opus"}, log_lines=logs)
    assert len(url) <= report.MAX_URL
    q = parse_qs(urlparse(url).query)
    assert q["title"] == ["[bug] "]
    assert "Engine: opus" in q["body"][0]
    assert "line 59" in q["body"][0]  # newest lines are kept, oldest dropped


def test_redaction(monkeypatch):
    monkeypatch.setattr(getpass, "getuser", lambda: "alice")
    home = os.path.expanduser("~")
    out = report.redact(f"open {home}/x and C:\\Users\\Bob\\f and /home/carol/y by alice")
    assert "alice" not in out and "Bob" not in out and "carol" not in out
    assert "<user>" in out


def test_cli_report_opens_browser(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    (tmp_path / "a.log").write_text("hello log\n", "utf-8")
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda u: seen.append(u) or True)
    assert cli.main(["--report", "--lang", "ru"]) == 0
    assert len(seen) == 1 and seen[0].startswith(report.ISSUE_URL)
    assert "ru" in parse_qs(urlparse(seen[0]).query)["body"][0]
    assert "hello log" in parse_qs(urlparse(seen[0]).query)["body"][0]
