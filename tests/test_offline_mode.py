"""#254: TLWU_OFFLINE=1 / unpinned Argos refusal / auto_update never pip-installs unasked. All mocked, no network."""
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu_setup import cli, downloader as dl

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_net(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(dl, "_open", boom)
    monkeypatch.setattr(cli.subprocess, "check_call", boom)


def test_setup_refuses_when_offline(monkeypatch, no_net, capsys):
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    assert cli.main(["--whisper", "small"]) == 2
    assert cli.main(["--deps"]) == 2
    assert "--online" in capsys.readouterr().err


def test_setup_online_flag_is_stripped_and_allowed(monkeypatch):
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    seen = []
    monkeypatch.setattr(dl, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["--online", "--pair", "en-zh"]) == 0
    assert "--online" not in seen[0] and "--pair" in seen[0]


def test_setup_not_blocked_without_flag(monkeypatch):
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    assert not cli.offline_refused(["--whisper", "small"])
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    assert not cli.offline_refused(["--verify"])


def test_get_pair_unpinned_needs_flag(no_net, tmp_path):
    with pytest.raises(SystemExit, match="--allow-unpinned"):
        dl.get_pair({}, tmp_path, "xx-yy")


def test_get_pair_unpinned_allowed_reaches_network(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dl, "_open", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(RuntimeError("net")))
    with pytest.raises(RuntimeError):
        dl.get_pair({}, tmp_path, "xx-yy", allow_unpinned=True)
    assert calls


def _run_update(tmp_path, offline, answer_input):
    """Real git repo whose upstream changes pyproject.toml; `python` is a stub that records pip calls."""
    up, work, bin_ = tmp_path / "up", tmp_path / "work", tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "pip.log"
    (bin_ / "python").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    (bin_ / "python").chmod(0o755)
    g = lambda *a, cwd: subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", *a], cwd=cwd, check=True,
                                       capture_output=True)
    up.mkdir(); g("init", "-q", "-b", "main", cwd=up)
    (up / "pyproject.toml").write_text("a"); g("add", ".", cwd=up); g("commit", "-qm", "1", cwd=up)
    g("clone", "-q", str(up), str(work), cwd=tmp_path)
    (up / "pyproject.toml").write_text("b"); g("commit", "-qam", "2", cwd=up)
    script = f'm(){{ echo "$1"; }}; mp(){{ echo "$1"; }}; . {ROOT}/scripts/auto_update.sh; tlwu_auto_update; echo "R=$TLWU_UPDATE_RESULT"'
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path), "M_UPD_PKG": "pkg", "M_UPD_PKG_ASK": "ask",
           "M_UPD_OK": "ok", "M_UPD_PKG_FAIL": "fail", "M_UPD_SKIP_NET": "net", "M_UPD_SKIP_DIVERGED": "div"}
    if offline:
        env["TLWU_OFFLINE"] = "1"
    r = subprocess.run(["bash", "-c", script], cwd=work, env=env, input=answer_input, capture_output=True, text=True)
    return r.stdout, (log.read_text() if log.exists() else "")


def test_auto_update_never_pips_unasked(tmp_path):
    out, pip = _run_update(tmp_path, False, "")   # stdin not a tty -> treated as refusal
    assert "R=updated" in out and pip == ""


def test_auto_update_skipped_when_offline(tmp_path):
    out, pip = _run_update(tmp_path, True, "")
    assert "R=forced_off" in out and pip == ""


def test_pack_prompt_offline_reports_missing_and_never_downloads(monkeypatch, no_net, tmp_path, capsys):
    from tinglongwu_setup import packs

    monkeypatch.setenv("TLWU_OFFLINE", "1")
    asked = []
    assert packs.prompt("ru", tmp_path, ask=lambda q: asked.append(q) or "y") == 1
    assert asked == []  # no download question at all
    err = capsys.readouterr().err
    assert "opus-en-ru" in err and "tlwu-setup --online --packs ru" in err
    assert cli.main(["--pack-prompt", "ru", "--models-dir", str(tmp_path)]) == 1  # not refused, just reported


def test_offline_only_pack_commands_still_run(monkeypatch, no_net, tmp_path, capsys):
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    assert cli.main(["--list", "--models-dir", str(tmp_path)]) == 0
    assert "zh  missing" in capsys.readouterr().out


@pytest.mark.parametrize("entry", ["packs", "talk"])
def test_installer_entry_points_opt_in_to_unpinned_pairs(monkeypatch, tmp_path, entry):
    """zh-en / en-ru have no committed pin yet: the pack and talk installers must pass --allow-unpinned explicitly
    (a bare tlwu-setup --pair zh-en is refused, see test_get_pair_unpinned_needs_flag)."""
    seen = []
    monkeypatch.setattr(dl, "get_pair", lambda man, mdir, pr, allow_unpinned=False: seen.append((pr, allow_unpinned)))
    if entry == "packs":
        from tinglongwu_setup import packs

        packs.install("zh", tmp_path)
    else:
        from types import SimpleNamespace

        from tinglongwu_setup import talk

        monkeypatch.setattr(dl, "get_kokoro", lambda *a, **k: None)
        monkeypatch.setattr(talk, "ensure_venv", lambda *a, **k: (_ for _ in ()).throw(StopIteration()))
        with pytest.raises(StopIteration):
            talk.main(SimpleNamespace(talk_venv=str(tmp_path / "v")), tmp_path)
    assert seen and all(flag for _, flag in seen)
    assert ("zh-en", True) in seen
