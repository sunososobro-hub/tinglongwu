"""#806: anonymous update-check counter. Client side (tinglongwu_setup.selfupdate + tinglongwu.gui.autoupdate) with
fake requests, plus the Cloudflare Worker's own tests (node --test, no npm packages)."""
import json
import shutil
import subprocess
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tinglongwu_setup import selfupdate as su

ROOT = Path(__file__).resolve().parent.parent
WORKER = "https://tlwu-update.example.workers.dev"
ENV = {"TLWU_UPDATE_COUNTER": WORKER}


def release(tag="v0.3.4"):
    return json.dumps({"tag_name": tag, "prerelease": False, "draft": False,
                       "assets": [{"name": n, "browser_download_url": f"https://x/{n}"}
                                  for n in (su.EXE_NAME, su.SUMS_NAME)]}).encode()


def getter(calls, worker_exc=None, worker_body=None):
    def get(url, timeout=su.CHECK_TIMEOUT_S):
        calls.append((url, timeout))
        if url.startswith(WORKER):
            if worker_exc:
                raise worker_exc
            return worker_body if worker_body is not None else release()
        if url.endswith(su.SUMS_NAME):
            return f"{'a' * 64}  {su.EXE_NAME}\n".encode()
        return release()
    return get


def test_counted_check_goes_to_worker_with_only_version_and_os():
    calls = []
    assert su.check("0.3.3", getter(calls), env=ENV, count=True) == "UPDATE 0.3.4"
    assert len(calls) == 1
    url, timeout = calls[0]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == WORKER + "/v1/latest"
    assert parse_qs(parts.query) == {"v": ["0.3.3"], "os": [su.os_tag()]}  # nothing else leaves the machine
    assert timeout == su.COUNTER_TIMEOUT_S


@pytest.mark.parametrize("exc", [urllib.error.URLError("dns"), TimeoutError(), ValueError("x")])
def test_worker_unreachable_falls_back_to_github(exc):
    calls = []
    assert su.check("0.3.3", getter(calls, worker_exc=exc), env=ENV, count=True) == "UPDATE 0.3.4"
    assert [u for u, _ in calls] == [f"{WORKER}/v1/latest?v=0.3.3&os={su.os_tag()}", su.API_LATEST]


def test_worker_bad_json_falls_back_to_github():
    calls = []
    assert su.check("0.3.3", getter(calls, worker_body=b"<html>"), env=ENV, count=True) == "UPDATE 0.3.4"
    assert calls[-1][0] == su.API_LATEST


@pytest.mark.parametrize("body", [b"{}", b"[]", b'{"error": "x"}'])
def test_worker_non_release_json_falls_back_to_github(body):
    calls = []
    assert su.check("0.3.3", getter(calls, worker_body=body), env=ENV, count=True) == "UPDATE 0.3.4"
    assert calls[-1][0] == su.API_LATEST


def test_uncounted_check_and_no_counter_url_go_straight_to_github():
    for env, count in ((ENV, False), ({}, True)):
        calls = []
        su.check("0.3.3", getter(calls), env=env, count=count)
        assert [u for u, _ in calls] == [su.API_LATEST]


def test_download_never_uses_the_worker(tmp_path):
    calls = []
    su.download(tmp_path, getter(calls), fetch=lambda url, dest, sha, on_bytes: dest, say=lambda s: None)
    assert not any(u.startswith(WORKER) for u, _ in calls)


def test_counter_url_rules():
    assert su.counter_url("0.3.3", env={}) is None or su.COUNTER_BASE.startswith("https://")
    assert su.counter_url("0.3.3", env={"TLWU_UPDATE_COUNTER": "http://plain"}) is None  # https only
    assert su.counter_url("dev", env=ENV, platform="darwin") == f"{WORKER}/v1/latest?v=other&os=mac"
    assert [su.os_tag(p) for p in ("win32", "darwin", "linux", "freebsd")] == ["windows", "mac", "linux", "other"]


def test_cli_passes_count_flag(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(su, "check", lambda cur, count=False: seen.setdefault("count", count) and "NONE" or "NONE")
    su.main(["--check", "0.3.3", "--count"])
    assert seen == {"count": True}
    seen.clear()
    su.main(["--check", "0.3.3"])
    assert seen == {"count": False}


def test_interval_constant_is_one_day():
    from tinglongwu.gui import autoupdate as au
    assert au.UPDATE_CHECK_INTERVAL_H == 24 and au.CHECK_EVERY_S == 24 * 3600
    assert au.should_auto_check({"auto_update": "1", "update_last_check": "1000"}, now=1000 + 23 * 3600, env={}) is False
    assert au.should_auto_check({"auto_update": "1", "update_last_check": "1000"}, now=1000 + 24 * 3600, env={}) is True


def _cmds(au, manual, cfg):
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd)

        class P:
            stdout = iter(["NONE\n"])

            def wait(self):
                return 0
        return P()
    au.run_check("0.3.3", lambda e: None, manual, popen=popen, cfg=cfg)
    return cmds


def test_auto_update_off_sends_nothing_and_manual_is_never_counted(monkeypatch):
    from tinglongwu.gui import autoupdate as au
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    # off: GUI start never checks, so no request at all
    assert au.should_auto_check({"auto_update": "0"}, now=10 ** 9, env={}) is False
    assert au.should_auto_check({}, now=10 ** 9, env={}) is False
    # the only counted call: automatic check with auto_update=1
    assert "--count" in _cmds(au, False, {"auto_update": "1"})[0]
    # a manual click asks GitHub directly, whatever the setting
    assert "--count" not in _cmds(au, True, {"auto_update": "1"})[0]
    assert "--count" not in _cmds(au, True, {"auto_update": "0"})[0]
    assert "--count" not in _cmds(au, False, {"auto_update": "0"})[0]


def test_readme_privacy_line():
    line = ("No personal data is collected. Update checks are counted anonymously (date, version, OS) to know how many "
            "installs are active.")
    assert line in (ROOT / "README.md").read_text("utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_worker_node_tests():
    r = subprocess.run(["node", "--test"], cwd=ROOT / "cloudflare" / "update-counter", capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
