"""Network guard: blocking, JSON log, worker inheritance, report URL, i18n. (marker: offline)"""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tinglongwu import i18n, netreport, offline, userdirs

pytestmark = pytest.mark.offline
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    offline._seen.clear()
    monkeypatch.setenv("TLWU_UI_LANG", "en")


def entries():
    return netreport.read_entries()


def test_blocks_external_and_logs_json(capsys):
    offline.set_mode("listen")
    with pytest.raises(offline.NetworkBlocked):
        socket.create_connection(("203.0.113.7", 8443), timeout=1)
    err = capsys.readouterr().err
    assert "203.0.113.7:8443" in err and "blocked" in err
    (e,) = entries()
    assert e["host"] == "203.0.113.7" and e["ip"] == "203.0.113.7" and e["port"] == 8443
    assert e["mode"] == "listen" and e["reported"] is False
    assert e["package"] == "tests" or e["package"] == "test_netguard" or e["package"]
    assert e["os"] and e["version"] and isinstance(e["stack"], list) and e["stack"]
    assert all(s.count(":") >= 2 for s in e["stack"])  # module:function:line, no source text
    json.dumps(e)


def test_hostname_logged_without_ip():
    with pytest.raises(offline.NetworkBlocked):
        socket.getaddrinfo("example.com", 443)
    (e,) = entries()
    assert e["host"] == "example.com" and e["ip"] is None and e["port"] == 443


def test_allows_loopback_and_unix(tmp_path):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    socket.create_connection(("127.0.0.1", srv.getsockname()[1]), timeout=2).close()
    srv.close()
    s6 = socket.socket(socket.AF_INET6)
    s6.close()
    if hasattr(socket, "AF_UNIX"):
        p = str(tmp_path / "s")
        u = socket.socket(socket.AF_UNIX)
        u.bind(p)
        u.listen(1)
        c = socket.socket(socket.AF_UNIX)
        c.connect(p)
        c.close()
        u.close()
    assert entries() == []


def test_allow_net_env_disables(monkeypatch):
    monkeypatch.setenv("TLWU_ALLOW_NET", "1")
    offline._check("203.0.113.9", 80)  # must not raise
    assert entries() == []


def test_duplicate_block_logged_once():
    for _ in range(3):
        with pytest.raises(offline.NetworkBlocked):
            socket.getaddrinfo("example.org", 80)
    assert len(entries()) == 1


def test_worker_subprocess_inherits_guard(tmp_path):
    """tts_worker.py's header installs the guard (same code path as the real worker, minus the TTS libraries)."""
    src = (ROOT / "tinglongwu" / "reverse" / "tts_worker.py").read_text("utf-8")
    head = src.split("_OUT = os.fdopen")[0]
    script = tmp_path / "w.py"
    # keep the worker's guard header verbatim, repoint __file__-relative root at the checkout
    script.write_text(head.replace("Path(__file__).resolve().parents[2]", f"Path({str(ROOT)!r})") + """
import socket
try:
    socket.create_connection(("203.0.113.5", 443), timeout=1)
    print("NOT BLOCKED")
except OSError as e:
    print("BLOCKED", type(e).__name__)
""", "utf-8")
    env = {**os.environ, "TLWU_LOG_DIR": str(tmp_path / "wl"), "TLWU_UI_LANG": "en"}
    env.pop("TLWU_ALLOW_NET", None)
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env)
    assert "BLOCKED NetworkBlocked" in out.stdout, out
    rec = json.loads((tmp_path / "wl" / offline.LOG_NAME).read_text("utf-8").splitlines()[0])
    assert rec["mode"] == "worker" and rec["host"] == "203.0.113.5"


def test_tts_client_sets_worker_mode():
    assert '"TLWU_MODE": "worker"' in (ROOT / "tinglongwu" / "reverse" / "tts_client.py").read_text("utf-8")


def _fake(host, n=1, stack=40, **kw):
    return {"ts": "2026-11-10T10:00:00", "host": host, "ip": None, "port": 443, "mode": "listen", "package": "pkg",
            "stack": [f"m{i}:f:{i}" for i in range(stack)], "version": "0.1.0", "sha": "abc1234", "os": "Linux",
            "reported": False, **kw}


def test_report_url_fields_and_truncation():
    ents = [_fake(f"host{i}.example.com") for i in range(60)]
    url, body = netreport.build_url(ents)
    assert len(url) <= netreport.MAX_URL
    u = urlparse(url)
    q = parse_qs(u.query)
    assert u.netloc == "github.com" and u.path == "/sunososobro-hub/tinglongwu/issues/new"
    assert q["labels"] == ["network-guard"] and "host0.example.com" in q["title"][0]
    assert q["body"][0] == body
    small, _ = netreport.build_url([_fake("a.example.com")])
    assert "a.example.com" in small and "truncated" not in small
    tiny_url, tiny_body = netreport.build_url(ents, max_url=900)
    assert len(tiny_url) <= 900


def test_report_flow_mark_and_dismiss(capsys, monkeypatch):
    with pytest.raises(offline.NetworkBlocked):
        socket.getaddrinfo("example.net", 80)
    assert netreport.main(["status"]) == 0
    assert "example.net" in capsys.readouterr().out
    opened = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda u: opened.append(u) or True)
    assert netreport.main(["send"]) == 0
    assert len(opened) == 1 and opened[0].startswith(netreport.ISSUE_URL)
    assert netreport.main(["status"]) == 1  # reported entries are not asked again
    offline._seen.clear()
    with pytest.raises(offline.NetworkBlocked):
        socket.getaddrinfo("example.net", 80)
    assert netreport.main(["dismiss"]) == 0
    assert netreport.main(["status"]) == 1
    assert len(entries()) == 2 and all(e["reported"] for e in entries())


@pytest.mark.parametrize("tag,lang", [("ru_RU.UTF-8", "ru"), ("ru", "ru"), ("zh_TW.UTF-8", "zh-TW"),
                                      ("zh-Hant-TW", "zh-TW"), ("zh_CN", "zh-TW"), ("en_US.UTF-8", "en"),
                                      ("de_DE", "en"), ("", "en"), ("C", "en")])
def test_normalize(tag, lang):
    assert i18n.normalize(tag) == lang


def test_ui_lang_override_and_messages(monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "ru")
    assert "заблокировал" in i18n.t("guard_blocked", host="h", port=1, pkg="p")
    monkeypatch.setenv("TLWU_UI_LANG", "zh-TW")
    assert "已擋下" in i18n.t("guard_blocked", host="h", port=1, pkg="p")
    monkeypatch.delenv("TLWU_UI_LANG")
    userdirs.write_config_value("ui_lang", "ru")
    assert i18n.ui_lang() == "ru"


def test_all_languages_have_same_keys():
    keys = set(i18n.MESSAGES["en"])
    for lang, tbl in i18n.MESSAGES.items():
        assert set(tbl) == keys, lang


def test_config_roundtrip():
    assert userdirs.read_config() == {}
    userdirs.write_config_value("auto_update", "0")
    userdirs.write_config_value("ui_lang", "en")
    assert userdirs.read_config() == {"auto_update": "0", "ui_lang": "en"}


def test_guard_is_installed_in_test_process():
    assert offline._installed and socket.create_connection.__module__ == "tinglongwu.offline"


def test_pipeline_smoke_makes_no_network_attempts(wav, tmp_path):
    """Whole pipeline with mocked models under the guard: if any library phones home it is blocked, logged, and this fails."""
    import io

    from tinglongwu.asr import Recognizer, Transcript
    from tinglongwu.capture import FileSource
    from tinglongwu.pipeline import run
    from tinglongwu.segment import Segmenter
    from tinglongwu.sink import TextSink
    from tinglongwu.translate import PivotTranslator

    class FakeASR(Recognizer):
        def transcribe(self, audio):
            return Transcript("привет мир", "ru")

    steps = {("ru", "en"): lambda x: "hello world", ("en", "zh"): lambda x: "你好世界"}
    out = io.StringIO()
    run(FileSource(str(wav)), Segmenter(chunk=1.0, use_vad=False), FakeASR(),
        PivotTranslator(tmp_path, "tw", lambda s, t: steps[(s, t)]), TextSink(out))
    assert "привет мир" in out.getvalue()
    assert entries() == []
