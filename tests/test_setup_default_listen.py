"""#766: tlwu-setup --default-listen downloads exactly the default listen models."""
import subprocess
import sys

from tinglongwu_setup import cli, defaults, downloader


def _fake(calls):
    def mk(key):
        def get(man, mdir):
            calls.append(key)
            d = mdir / key
            if not d.exists():  # already present -> nothing downloaded
                d.mkdir(parents=True)
                (d / "model.bin").write_bytes(b"x" * (2 * defaults.MB))
            man[key] = {"ok": True}
        return get
    return {k: mk(k) for k in defaults.DEFAULT_LISTEN_KEYS}


def test_constants_and_keys():
    assert (defaults.DEFAULT_LISTEN_SOURCE, defaults.DEFAULT_LISTEN_TARGET) == ("en", "zh-TW")
    assert defaults.DEFAULT_LISTEN_KEYS == ("whisper-small", "hymt2-1.8b")
    assert defaults.DEFAULT_LISTEN_MT == downloader.HYMT_DIR


def test_requests_exactly_default_keys_and_reports(tmp_path):
    calls, lines = [], []
    assert defaults.run(tmp_path, _fake(calls), out=lines.append) == 0
    assert calls == list(defaults.DEFAULT_LISTEN_KEYS)
    assert lines[0].startswith("[1/2]") and lines[1].startswith("[2/2]")
    assert lines[-1].startswith("READY default-listen seconds=") and lines[-1].endswith("download_mb=4")


def test_second_run_downloads_zero_mb(tmp_path):
    defaults.run(tmp_path, _fake([]), out=lambda s: None)
    lines = []
    defaults.run(tmp_path, _fake([]), out=lines.append)
    assert lines[-1].endswith("download_mb=0")


def test_cli_flag_never_runs_pip(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(defaults, "run", lambda mdir: seen.append(mdir) or 0)
    monkeypatch.setattr(cli, "_pip", lambda *a: (_ for _ in ()).throw(AssertionError("pip")))
    assert cli.main(["--default-listen", "--models-dir", str(tmp_path)]) == 0
    assert seen == [tmp_path]
