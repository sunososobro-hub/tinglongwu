"""#673: pack-on-demand dialog logic (fake manifest, fake network, fake ask; no Tk) and the resumable download."""
from __future__ import annotations

import hashlib

import pytest

from tinglongwu.gui import pack_on_demand as pod
from tinglongwu_setup import resume

SIZES = {"ja": 1100, "ru": 1160, "zh": 0, "en": 0}


def _run(installed, answer, rc=0, src="ja", tgt="zh-tw", ui="en", mbps=None):
    asked, dl = [], []

    def ask(text, ok, no):
        asked.append((text, ok, no))
        return answer

    def download(pack):
        dl.append(pack)
        if rc == 0:
            installed.add(pack)
        return rc

    ok = pod.ensure_packs(src, tgt, None, ask, download, ui_lang=ui, mbps=mbps,
                          missing=lambda p: [] if (p in installed or SIZES[p] == 0) else [p],
                          size_mb=SIZES.get)
    return ok, asked, dl


def test_dialog_shown_and_download_runs_on_yes():
    installed = set()
    ok, asked, dl = _run(installed, True)
    assert ok and dl == ["ja"] and len(asked) == 1
    assert asked[0][0] == ("This needs the ja pack (about 1100 MB, about 2 min). Needs internet to download once, "
                           "works offline afterwards.")  # 1100 MB / 10 MB/s fallback = 110 s


def test_installed_pack_asks_nothing():
    ok, asked, dl = _run({"ja"}, True)
    assert ok and asked == [] and dl == []


def test_not_now_keeps_selection_and_downloads_nothing():
    ok, asked, dl = _run(set(), False)
    assert ok is False and dl == [] and len(asked) == 1


def test_failed_download_returns_false():
    ok, _, dl = _run(set(), True, rc=1)
    assert ok is False and dl == ["ja"]


def test_estimate_uses_measured_speed_else_fallback():
    assert pod.est_minutes(1200, 5) == 4 and pod.est_minutes(1200) == 2 and pod.est_minutes(1200, 0) == 2
    assert pod.est_minutes(1, 100) == 1


@pytest.mark.parametrize("lang,word", [("en", "Needs internet"), ("ru", "интернет"), ("zh-TW", "連網")])
def test_dialog_text_in_three_languages(lang, word):
    _, asked, _ = _run(set(), False, ui=lang)
    assert word in asked[0][0] and "1100" in asked[0][0]
    assert pod.BUTTONS[lang][1] == asked[0][2]


def test_needed_packs_maps_codes():
    assert pod.needed_packs("auto", "zh-tw") == ["zh"]
    assert pod.needed_packs("ru", "zh-cn") == ["ru", "zh"]
    assert pod.needed_packs("de", "en") == ["en"]


class FakeNet:
    """Serves DATA; honours Range; can drop the connection after `cut` bytes on the first call."""

    def __init__(self, data, cut=None, ignore_range=False):
        self.data, self.cut, self.ignore_range, self.calls = data, cut, ignore_range, []

    def __call__(self, url, start):
        self.calls.append(start)
        net = self
        begin = 0 if net.ignore_range else start
        body = net.data[begin:]
        limit = net.cut if len(net.calls) == 1 and net.cut is not None else None

        class R:
            status = 206 if begin else 200

            def __enter__(s):
                s.i = 0
                return s

            def __exit__(s, *a):
                return False

            def read(s, n):
                end = len(body) if limit is None else min(limit, len(body))
                if limit is not None and s.i >= limit:
                    raise ConnectionError("dropped")
                b = body[s.i:min(s.i + n, end)]
                s.i += len(b)
                return b

        return R()


DATA = bytes(range(256)) * 40
SHA = hashlib.sha256(DATA).hexdigest()


def test_interrupted_download_resumes_with_range(tmp_path):
    net = FakeNet(DATA, cut=3000)
    dest = tmp_path / "pack.bin"
    with pytest.raises(ConnectionError):
        resume.fetch("u", dest, SHA, opener=net, chunk=1000)
    assert not dest.exists() and (tmp_path / "pack.bin.part").stat().st_size == 3000
    resume.fetch("u", dest, SHA, opener=net, chunk=1000)
    assert net.calls == [0, 3000] and dest.read_bytes() == DATA and not (tmp_path / "pack.bin.part").exists()


def test_server_ignoring_range_restarts(tmp_path):
    net = FakeNet(DATA, cut=2000, ignore_range=True)
    dest = tmp_path / "p.bin"
    with pytest.raises(ConnectionError):
        resume.fetch("u", dest, SHA, opener=net, chunk=1000)
    resume.fetch("u", dest, SHA, opener=net, chunk=1000)
    assert dest.read_bytes() == DATA


def test_corrupt_file_deleted_and_retried_once(tmp_path):
    calls = []

    def opener(url, start):
        calls.append(start)
        return FakeNet(DATA if len(calls) > 1 else DATA[:-1] + b"\x00")(url, start)

    dest = tmp_path / "p.bin"
    resume.fetch("u", dest, SHA, opener=opener)
    assert dest.read_bytes() == DATA and calls == [0, 0]


def test_corrupt_twice_raises_and_leaves_nothing(tmp_path):
    dest = tmp_path / "p.bin"
    with pytest.raises(ValueError):
        resume.fetch("u", dest, "0" * 64, opener=FakeNet(DATA))
    assert not dest.exists() and not (tmp_path / "p.bin.part").exists()


def test_pack_download_cmd_runs_setup_packs_child():
    from tinglongwu.gui import core

    cmd = core.pack_download_cmd("ja", "/m", bundled=False)
    assert cmd[1:3] == ["-m", "tinglongwu_setup"] and cmd[3:5] == ["--packs", "ja"]
    assert "--whisper" not in cmd and cmd[-2:] == ["--models-dir", "/m"]
    assert core.pack_download_cmd("ru", "/m", bundled=True)[1:4] == ["--run-setup", "--packs", "ru"]


def test_run_pack_download_reports_lines_and_done():
    import io

    from tinglongwu.gui import core

    class P:
        stdout = io.BytesIO(b"pack: 10%\rpack: 50%\n")

        def wait(self):
            return 0

    got = []
    assert core.run_pack_download("ja", got.append, "/m", popen=lambda *a, **k: P(), bundled=False) == 0
    assert got == [("pack_line", "pack: 50%"), ("pack_done", "ja", 0)]


def test_pack_table_matches_setup_and_reads_local_manifest(tmp_path):
    import json

    from tinglongwu_setup import packs

    assert pod.PACKS == packs.PACKS and pod.SIZE_MB == packs.SIZE_MB and pod.OPUS_MB == packs.OPUS_MB
    assert all(pod._pack_mb(p) == packs.pack_size_mb(p) for p in packs.PACKS)
    assert pod.first_missing("ja", "zh-tw", tmp_path) == ("ja", 1100)
    (tmp_path / "hymt2-1.8b").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps({"hymt2-1.8b": {"files": {}}}))
    assert pod.first_missing("ja", "zh-tw", tmp_path) is None
    assert pod.first_missing("ru", "en", tmp_path) == ("ru", 1160)
