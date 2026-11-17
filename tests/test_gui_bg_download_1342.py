"""#1358: background download of the missing default listen models (fake child, no network, no Tk)."""
from __future__ import annotations

import hashlib
import json

from tinglongwu.gui import bg_download as bgd
from tinglongwu.gui import core
from tinglongwu_setup import defaults, resume


class FakeChild:
    def __init__(self, lines, rc=0):
        self.stdout, self.rc = iter(lines), rc

    def wait(self):
        return self.rc


def _mgr(tmp_path, lines=(), rc=0, spawned=None):
    status, ready = [], []

    def popen(cmd):
        if spawned is not None:
            spawned.append(cmd)
        return FakeChild(list(lines), rc)

    m = bgd.BackgroundDownloads(["child"], status.append, ready.append, tmp_path, popen=popen, threaded=False)
    return m, status, ready


def test_two_start_presses_start_one_child_per_key(tmp_path):
    spawned = []
    m, _, _ = _mgr(tmp_path, spawned=spawned)
    m.active.update(bgd.DEFAULT_KEYS)  # a child is already running for both keys
    assert m.start() == [] and spawned == []
    m.active.clear()
    assert m.start() == list(bgd.DEFAULT_KEYS) and len(spawned) == 1
    assert m.started == {"whisper-small": 1, "hymt2-1.8b": 1}


def test_second_press_while_running_does_not_spawn(tmp_path):
    spawned = []
    m, _, _ = _mgr(tmp_path, spawned=spawned)
    m.threaded = False

    # simulate "child still running" by pre-marking, then pressing Start twice
    m.active.update(bgd.DEFAULT_KEYS)
    m.start()
    m.start()
    assert spawned == []


def test_percent_parsed_from_child_line(tmp_path):
    assert bgd.parse_percent("download hymt2-1.8b: 35% (387/1100 MB)") == ("hymt2-1.8b", 35)
    assert bgd.parse_percent("something else") is None
    m, status, _ = _mgr(tmp_path, ["download hymt2-1.8b: 35% (387/1100 MB)\n"])
    m.start()
    assert "translation downloading 35%" in status


def test_exit_zero_calls_handover_hook_for_each_key(tmp_path):
    m, status, ready = _mgr(tmp_path, ["download hymt2-1.8b: 100% (1100/1100 MB)\n"], rc=0)
    m.start()
    assert ready == list(bgd.DEFAULT_KEYS) and m.active == set()


def test_exit_nonzero_shows_error_and_retry_and_allows_new_child(tmp_path):
    spawned = []
    m, status, ready = _mgr(tmp_path, ["boom: no network\n"], rc=2, spawned=spawned)
    m.start()
    assert ready == [] and any("no network" in s and "retry" in s for s in status)
    assert m.start() == list(bgd.DEFAULT_KEYS) and len(spawned) == 2  # retry starts a fresh child


def test_installed_keys_are_not_downloaded(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({k: {} for k in bgd.DEFAULT_KEYS}), "utf-8")
    for k in bgd.DEFAULT_KEYS:
        (tmp_path / k).mkdir()
    m, _, _ = _mgr(tmp_path)
    assert m.start() == []


def test_keys_match_setup_defaults():
    assert bgd.DEFAULT_KEYS == defaults.DEFAULT_LISTEN_KEYS


def test_cmd_runs_default_listen_child():
    cmd = core.default_listen_cmd("/m", bundled=False)
    assert cmd[1:3] == ["-m", "tinglongwu_setup"] and "--default-listen" in cmd and "--whisper" not in cmd
    assert cmd[-2:] == ["--models-dir", "/m"]


def test_partial_file_resumes(tmp_path):
    data = bytes(range(256)) * 20
    sha = hashlib.sha256(data).hexdigest()
    calls = []

    class Net:
        def __init__(self, start):
            self.b, self.cut = data[start:], None

        def read(self, n=-1):
            out, self.b = self.b[:n], self.b[n:]
            return out

        headers = {}
        status = 206

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

    def opener(url, start):
        calls.append(start)
        return Net(start)

    dest = tmp_path / "m.bin"
    (tmp_path / "m.bin.part").write_bytes(data[:1000])
    resume.fetch("u", dest, sha, opener=opener, chunk=500)
    assert calls == [1000] and dest.read_bytes() == data  # continued from the partial file, not restarted


def test_real_fetch_progress_lines_feed_the_parser(tmp_path):
    data = bytes(range(256)) * 4096  # 1 MiB
    sha = hashlib.sha256(data).hexdigest()

    class Net:
        status, headers = 200, {"Content-Length": str(len(data))}

        def __init__(self):
            self.b = data

        def read(self, n=-1):
            out, self.b = self.b[:n], self.b[n:]
            return out

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    lines = []
    resume.fetch("u", tmp_path / "m.gguf", sha, opener=lambda u, s: Net(), chunk=65536, label="hymt2-1.8b",
                 say=lines.append)
    got = [bgd.parse_percent(x) for x in lines]
    assert got and all(g and g[0] == "hymt2-1.8b" for g in got) and got[-1][1] == 100


def test_default_popen_uses_download_env(tmp_path, monkeypatch):
    import subprocess

    seen = {}

    class P:
        stdout = iter([])

        def wait(self):
            return 0

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(subprocess, "Popen", lambda c, **kw: (seen.update(kw), P())[1])
    m = bgd.BackgroundDownloads(["x"], lambda s: None, lambda k: None, tmp_path, threaded=False)
    m.start()
    assert "HF_HUB_OFFLINE" not in seen["env"]


def test_tqdm_bar_of_the_whisper_download_shows_percent(tmp_path):
    m, status, _ = _mgr(tmp_path, ["model.bin:  40%|####      | 193M/484M [00:10<00:15]\n"])
    m.start()
    assert "speech model downloading 40%" in status
