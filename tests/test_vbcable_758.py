"""#804 (part 1 of #758): one-click VB-CABLE install for speak mode. All mocked: no network, no driver, no UAC."""
import hashlib
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tinglongwu import vbcable
from tinglongwu_setup import vbcable as setup_vb

PAYLOAD = b"fake VB-CABLE package bytes"
PIN = hashlib.sha256(PAYLOAD).hexdigest()
WITH_CABLE = ["Speakers (Realtek(R) Audio)", "CABLE Input (VB-Audio Virtual Cable)"]
NO_CABLE = ["Speakers (Realtek(R) Audio)", "Headphones (USB Audio)"]


@pytest.fixture
def no_net(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)


class Flow:
    """Records what the flow did; `data` is what the fake download writes."""

    def __init__(self, tmp: Path, data: bytes = PAYLOAD, answer: bool = True, after=WITH_CABLE, rc: int = 0):
        self.tmp, self.data, self.answer, self.after, self.rc = tmp, data, answer, after, rc
        self.asked, self.downloads, self.installed, self.opened = [], 0, [], []

    def ask(self, text):
        self.asked.append(text)
        return self.answer

    def download(self):
        self.downloads += 1
        p = self.tmp / "VBCABLE_Driver_Pack45.zip"
        p.write_bytes(self.data)
        return p

    def install(self, path):
        self.installed.append(path)
        return self.rc

    def run(self, names=NO_CABLE, pin=PIN, env=None, ui_lang="zh-TW"):
        return vbcable.ensure_cable(names, self.ask, self.download, self.install, self.opened.append,
                                    list_names=lambda: self.after, ui_lang=ui_lang, pin=pin, env=env or {},
                                    platform="win32")


# (1) detection from a device-name list
@pytest.mark.parametrize("names,present", [(WITH_CABLE, True), (NO_CABLE, False), ([], False),
                                          (["cable input (vb-audio virtual cable)"], True),
                                          (["CABLE Output (VB-Audio Virtual Cable)"], False)])
def test_has_cable(names, present):
    assert vbcable.has_cable(names) is present


def test_offer_only_on_windows_with_a_known_list_and_no_cable():
    assert vbcable.needs_offer(NO_CABLE, "win32")
    assert not vbcable.needs_offer(WITH_CABLE, "win32")
    assert not vbcable.needs_offer([], "win32")  # no audio stack: unknown, do not nag
    assert not vbcable.needs_offer(NO_CABLE, "darwin")  # macOS BlackHole: out of scope
    assert not vbcable.needs_offer(NO_CABLE, "linux")  # Linux makes its own tlwu_mic


def test_cable_present_starts_speak_without_asking(tmp_path):
    f = Flow(tmp_path)
    assert f.run(names=WITH_CABLE) == "speak"
    assert f.asked == [] and f.downloads == 0


def test_happy_path_installs_then_speaks(tmp_path):
    f = Flow(tmp_path)
    assert f.run() == "speak"
    assert f.asked == ["需要安裝虛擬麥克風（免費，約 1 MB）[安裝]"]  # the GUI dialog text (zh-TW UI)
    assert f.downloads == 1 and len(f.installed) == 1


# (2) SHA-256 mismatch: abort, nothing run
def test_sha_mismatch_runs_nothing(tmp_path):
    f = Flow(tmp_path, data=b"tampered package")
    assert f.run() == "listen"
    assert f.installed == []
    assert not (tmp_path / "VBCABLE_Driver_Pack45.zip").exists()  # the bad file is deleted
    assert vbcable.install_flow(Flow(tmp_path, data=b"x").download, f.install, PIN, {}) == "bad-sha"


def test_setup_child_install_refuses_a_mismatched_zip(tmp_path, capsys):
    z = tmp_path / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("VBCABLE_Setup_x64.exe", b"MZ")
    ran = []
    assert setup_vb.install(z, "0" * 64, run=lambda cmd: ran.append(cmd), machine="AMD64") == 1
    assert ran == [] and "mismatch" in capsys.readouterr().out
    assert not z.with_suffix("").exists()  # not even extracted


def test_setup_child_install_runs_the_setup_elevated_when_the_pin_matches(tmp_path):
    z = tmp_path / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("VBCABLE_Setup_x64.exe", b"MZ")
        zf.writestr("VBCABLE_Setup.exe", b"MZ")
    pin = hashlib.sha256(z.read_bytes()).hexdigest()
    ran = []
    rc = setup_vb.install(z, pin, run=lambda cmd: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0),
                          machine="AMD64")
    assert rc == 0 and len(ran) == 1
    ps = ran[0][-1]
    assert "VBCABLE_Setup_x64.exe" in ps and "-Verb RunAs" in ps and "'-i','-h'" in ps


def test_setup_child_rejects_zip_slip(tmp_path):
    z = tmp_path / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil.exe", b"MZ")
    with pytest.raises(ValueError):
        setup_vb.extract(z)


def test_setup_child_download_mismatch_prints_no_ready(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)

    def fetch(url, dest, sha):
        raise ValueError("SHA256 mismatch for pack.zip after a retry")
    monkeypatch.setattr(setup_vb.resume, "fetch", fetch)
    assert setup_vb.main(["--download", str(tmp_path), "--url", "https://example.invalid/p.zip",
                          "--sha256", PIN]) == 1
    out = capsys.readouterr().out
    assert "READY" not in out and "FAIL" in out


# (3) offline mode: never download
def test_offline_never_downloads(tmp_path, no_net):
    f = Flow(tmp_path)
    assert f.run(env={"TLWU_OFFLINE": "1"}) == "listen"
    assert f.downloads == 0 and f.installed == []
    assert f.opened == [vbcable.VBCABLE_PAGE]


def test_setup_child_download_refuses_offline(tmp_path, monkeypatch, no_net):
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    monkeypatch.setattr(setup_vb.resume, "fetch", lambda *a, **k: pytest.fail("download attempted"))
    assert setup_vb.main(["--download", str(tmp_path), "--url", vbcable.VBCABLE_URL, "--sha256", PIN]) == 2


# (4) no pin yet: open the official page instead
def test_empty_pin_opens_the_page(tmp_path, no_net):
    f = Flow(tmp_path)
    assert f.run(pin="") == "listen"
    assert f.downloads == 0 and f.opened == ["https://vb-audio.com/Cable/"]


def test_constants_are_named_and_official():
    assert vbcable.VBCABLE_URL.startswith("https://download.vb-audio.com/")
    assert isinstance(vbcable.VBCABLE_SHA256, str)
    assert vbcable.VBCABLE_SHA256 == "" or len(vbcable.VBCABLE_SHA256) == 64


# (5) cancel / failure: stay in listen mode
def test_cancel_stays_listen(tmp_path):
    f = Flow(tmp_path, answer=False)
    assert f.run() == "listen"
    assert f.downloads == 0 and f.opened == []


@pytest.mark.parametrize("kw", [{"rc": 1}, {"after": NO_CABLE}])  # UAC cancelled / CABLE still missing
def test_failed_install_stays_listen(tmp_path, kw):
    assert Flow(tmp_path, **kw).run() == "listen"


def test_download_error_stays_listen(tmp_path):
    def broken():
        raise ConnectionError("reset")
    assert vbcable.ensure_cable(NO_CABLE, lambda t: True, broken, lambda p: 0, lambda u: None,
                                list_names=lambda: WITH_CABLE, pin=PIN, env={}, platform="win32") == "listen"


def test_child_commands_go_through_the_setup_package(tmp_path):
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout=f"PROGRESS 50\nREADY {tmp_path / 'x.zip'}\n")
    assert vbcable.child_download(tmp_path, run=run) == str(tmp_path / "x.zip")
    cmd, kw = seen[0]
    assert cmd[:3] == [sys.executable, "-m", "tinglongwu_setup.vbcable"] and "--download" in cmd
    assert "HF_HUB_OFFLINE" not in kw["env"]  # the child gets a clean env; this process stays guarded


def test_english_ui_text():
    assert "[Install]" in vbcable.dialog_text("en") and "CABLE Output" in vbcable.howto_text("en")
    assert vbcable.dialog_text("xx") == vbcable.dialog_text("en")
