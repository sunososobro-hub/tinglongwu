"""scripts/firewall_test.py (#7): block -> run listen/speak/meeting -> verdict; firewall rule always removed."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import firewall_test as ft


def _unshare_works():
    try:
        return subprocess.run(["unshare", "-n", "-r", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_ns = pytest.mark.skipif(not sys.platform.startswith("linux") or not _unshare_works(),
                              reason="needs Linux user namespaces")


class FakeRun:
    """Stands in for subprocess.run: answers the probe, records netsh calls, plays tlwu and writes its outputs."""

    def __init__(self, probe_blocked="BLOCKED", netsh_rc=0, listen=None, reverse=None, guard=None, rc=0):
        self.calls, self.probe_blocked, self.netsh_rc = [], probe_blocked, netsh_rc
        self.listen, self.reverse, self.guard, self.rc = listen, reverse, guard, rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        ok = lambda out="": SimpleNamespace(returncode=0, stdout=out, stderr="")  # noqa: E731
        if cmd[0] == "netsh":
            return SimpleNamespace(returncode=self.netsh_rc if "add" in cmd else 0, stdout="denied", stderr="")
        if ft.PROBE in cmd:
            blocked = any(c == "unshare" for c in cmd) or self.rules_active()
            return ok(self.probe_blocked if blocked else "OPEN")
        env = kw.get("env", {})
        if self.guard:
            log = Path(env["TLWU_LOG_DIR"])
            log.mkdir(parents=True, exist_ok=True)
            (log / "netguard.jsonl").write_text(json.dumps(self.guard) + "\n", encoding="utf-8")
        if "--save" in cmd and self.listen is not None:
            Path(cmd[cmd.index("--save") + 1]).write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self.listen), encoding="utf-8")
        if "--save-result" in cmd and self.reverse is not None:
            Path(cmd[cmd.index("--save-result") + 1]).write_text(json.dumps({"items": self.reverse}),
                                                                 encoding="utf-8")
        return SimpleNamespace(returncode=self.rc, stdout="", stderr="boom" if self.rc else "")

    def rules_active(self):
        state = 0
        for c in self.calls:
            if c[0] == "netsh":
                state = 1 if "add" in c else 0
        return state == 1

    def netsh(self):
        return [c for c in self.calls if c[0] == "netsh"]


@pytest.fixture
def zh_wav(wav):
    return wav  # 44.1 kHz tone; the script resamples it to 16 kHz


LISTEN_OK = [{"lang": "ru", "src": "привет мир", "dst": "你好世界"}]
REV_OK = [{"file": "speak16k.wav", "zh": "你好", "en": "hello", "tts": []}]


def test_linux_all_modes_pass(wav, zh_wav):
    run = FakeRun(listen=LISTEN_OK, reverse=REV_OK)
    rc = ft.main(["--listen-wav", str(wav), "--speak-wav", str(zh_wav)], run=run, platform="linux")
    assert rc == 0
    tlwu = [c for c in run.calls if "-m" in c]
    assert [c[c.index("-m") + 1] for c in tlwu] == ["tinglongwu", "tinglongwu.reverse.proto",
                                                     "tinglongwu.reverse.proto"]
    assert all(c[:3] == ["unshare", "-n", "-r"] for c in tlwu)  # every mode ran without network
    meeting = tlwu[2]
    assert meeting[meeting.index("--to") + 1] == "follow" and "--no-tts" in meeting


def test_speak_wav_resampled_to_16k(zh_wav, tmp_path):
    import soundfile as sf

    a = ft.argparse.Namespace(listen_wav=None, speak_wav=zh_wav, to="zh", mt="opus", compute="cpu",
                              tts_python="", tts_model="")
    modes = ft.plan(a, tmp_path)
    w = Path(modes[0].cmd[modes[0].cmd.index("--input") + 1])
    info = sf.info(str(w))
    assert (info.samplerate, info.channels) == (16000, 1) and abs(info.duration - 3.0) < 0.01


def test_empty_translation_fails(wav):
    run = FakeRun(listen=[{"lang": "ru", "src": "привет", "dst": ""}])
    assert ft.main(["--listen-wav", str(wav)], run=run, platform="linux") == 1


def test_crash_fails_with_output_tail(wav, capsys):
    rc = ft.main(["--listen-wav", str(wav)], run=FakeRun(rc=1), platform="linux")
    assert rc == 1 and "exit 1: boom" in capsys.readouterr().out


def test_guard_entry_fails_even_if_translated(wav, capsys):
    run = FakeRun(listen=LISTEN_OK, guard={"package": "hf", "host": "huggingface.co", "port": 443})
    assert ft.main(["--listen-wav", str(wav)], run=run, platform="linux") == 1
    assert "hf->huggingface.co:443" in capsys.readouterr().out


def test_tts_required_when_given(zh_wav, tmp_path):
    py = sys.executable
    run = FakeRun(reverse=REV_OK)  # translated, but no tts entries
    rc = ft.main(["--speak-wav", str(zh_wav), "--tts-python", py, "--tts-model", str(tmp_path)], run=run,
                 platform="linux")
    assert rc == 1
    assert any("--tts-engine" in c for c in run.calls)


def test_windows_rule_added_then_removed(wav):
    run = FakeRun(listen=LISTEN_OK)
    assert ft.main(["--listen-wav", str(wav)], run=run, platform="win32", admin=lambda: True) == 0
    ns = run.netsh()
    adds = [c for c in ns if "add" in c]
    assert adds and all("dir=out" in c and "action=block" in c for c in adds)
    assert any(f"program={sys.executable}" in c for c in adds)
    assert "delete" in ns[-1] and not run.rules_active()


def test_windows_rule_removed_when_block_not_active(wav, capsys):
    run = FakeRun(probe_blocked="OPEN", listen=LISTEN_OK)  # the rule does not stop Python
    assert ft.main(["--listen-wav", str(wav)], run=run, platform="win32", admin=lambda: True) == 2
    assert "block not active" in capsys.readouterr().err
    assert not run.rules_active() and not [c for c in run.calls if "-m" in c]


def test_windows_rule_removed_on_ctrl_c(wav):
    class Interrupt(FakeRun):
        def __call__(self, cmd, **kw):
            if "-m" in cmd:
                raise KeyboardInterrupt
            return super().__call__(cmd, **kw)

    run = Interrupt()
    with pytest.raises(KeyboardInterrupt):
        ft.main(["--listen-wav", str(wav)], run=run, platform="win32", admin=lambda: True)
    assert not run.rules_active() and "delete" in run.netsh()[-1]


def test_windows_add_failure_cleans_up(wav, capsys):
    run = FakeRun(netsh_rc=1)
    assert ft.main(["--listen-wav", str(wav)], run=run, platform="win32", admin=lambda: True) == 2
    assert "could not add the firewall rule" in capsys.readouterr().err and "delete" in run.netsh()[-1]


@pytest.mark.parametrize("platform,admin,msg", [("win32", False, "administrator"), ("darwin", True,
                                                                                    "--already-blocked")])
def test_setup_errors(wav, platform, admin, msg, capsys):
    run = FakeRun()
    assert ft.main(["--listen-wav", str(wav)], run=run, platform=platform, admin=lambda: admin) == 2
    assert msg in capsys.readouterr().err and not run.netsh()


def test_already_blocked_but_online_is_refused(wav, capsys):
    run = FakeRun()  # NoBlock: the probe reaches the internet
    assert ft.main(["--listen-wav", str(wav), "--already-blocked"], run=run, platform="darwin") == 2
    assert "block not active" in capsys.readouterr().err


def test_no_input_is_setup_error(capsys):
    assert ft.main([], run=FakeRun(), platform="linux") == 2


@needs_ns
def test_real_namespace_blocks_python():
    assert ft.probe(ft.NamespaceBlock()) == "BLOCKED"


@needs_ns
def test_real_guard_attempt_inside_namespace_fails_the_mode(tmp_path):
    """A real tlwu process that tries the network inside the block: refused AND reported via netguard.jsonl."""
    code = ("import tinglongwu, socket\n"
            "try:\n    socket.create_connection(('1.1.1.1', 443), timeout=2)\nexcept OSError:\n    pass\n")
    m = ft.Mode("listen", [sys.executable, "-c", code], lambda: (True, "translated"), tmp_path / "log")
    ok, detail = ft.run_mode(m, ft.NamespaceBlock(), 60)
    assert not ok and "1.1.1.1:443" in detail
