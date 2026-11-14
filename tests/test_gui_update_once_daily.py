"""#779: update prompt at most once a day; background download is applied at the next launch. Fake clock, no network."""
import pytest

from tinglongwu import userdirs
from tinglongwu.gui import autoupdate as au

DAY = 24 * 3600


@pytest.fixture(autouse=True)
def cfg_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)


def test_constants():
    assert au.UPDATE_PROMPT_MIN_INTERVAL_S == DAY
    assert au.PROMPT_KEY == "update_last_prompt"


def test_four_releases_in_one_day_show_one_line():
    t0 = 1_800_000_000.0
    shown = [au.claim_prompt(userdirs.read_config(), now=t0 + i * 3 * 3600) for i in range(4)]
    assert shown == [True, False, False, False]
    assert au.claim_prompt(userdirs.read_config(), now=t0 + DAY + 1) is True


def test_pending_newer_and_existing_is_applied(tmp_path):
    exe = tmp_path / "setup-0.3.6.exe"
    exe.write_bytes(b"x")
    au.set_pending("0.3.6", str(exe))
    assert au.pending_to_apply(userdirs.read_config(), "0.3.5") == str(exe)


def test_pending_stale_or_missing_is_cleared_silently(tmp_path):
    exe = tmp_path / "setup-0.3.5.exe"
    exe.write_bytes(b"x")
    au.set_pending("0.3.5", str(exe))
    assert au.pending_to_apply(userdirs.read_config(), "0.3.5") is None  # not newer
    assert not userdirs.read_config().get(au.PENDING_KEY)
    au.set_pending("0.3.9", str(tmp_path / "gone.exe"))
    assert au.pending_to_apply(userdirs.read_config(), "0.3.5") is None  # file missing
    assert not userdirs.read_config().get(au.PENDING_KEY)


def test_newer_release_replaces_pending_and_deletes_old_file(tmp_path):
    old, new = tmp_path / "a.exe", tmp_path / "b.exe"
    old.write_bytes(b"1")
    new.write_bytes(b"2")
    au.set_pending("0.3.6", str(old))
    au.set_pending("0.3.7", str(new))
    assert not old.exists() and new.exists()
    assert au.get_pending(userdirs.read_config()) == ("0.3.7", str(new))


def test_garbage_pending_is_cleared():
    userdirs.write_config_value(au.PENDING_KEY, "nonsense")
    assert au.pending_to_apply(userdirs.read_config(), "0.3.5") is None
    assert not userdirs.read_config().get(au.PENDING_KEY)


def test_older_download_never_replaces_newer_pending(tmp_path):
    new, old = tmp_path / "new.exe", tmp_path / "old.exe"
    new.write_bytes(b"2")
    old.write_bytes(b"1")
    au.set_pending("0.3.7", str(new))
    au.set_pending("0.3.6", str(old))
    assert au.get_pending(userdirs.read_config()) == ("0.3.7", str(new))
    assert new.exists()
