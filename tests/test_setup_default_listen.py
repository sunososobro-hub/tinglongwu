"""#766: tlwu-setup --default-listen downloads exactly the default listen models."""
import subprocess
import sys

import io

from tinglongwu_setup import cli, defaults, downloader
from tlwu_app import main as app_main, tiers


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


# ---- #771: the core wizard runs `tlwu.exe setup --default-listen`
def test_tlwu_exe_setup_routes_default_listen_to_the_setup_cli(tmp_path, monkeypatch):
    """tlwu.exe setup used to hand every flag to downloader.main, which exits 2 on --default-listen (argparse)."""
    seen = []
    monkeypatch.setattr(defaults, "run", lambda mdir: seen.append(mdir) or 0)
    monkeypatch.setattr(cli, "_pip", lambda *a: (_ for _ in ()).throw(AssertionError("pip")))
    assert app_main.run_setup_worker(["--default-listen", "--models-dir", str(tmp_path)]) == 0
    assert seen == [tmp_path]


def test_other_setup_flags_still_go_to_the_downloader(monkeypatch):
    got = []
    monkeypatch.setattr(downloader, "main", lambda a: got.append(a) or 0)
    assert app_main.run_setup_worker(["--whisper", "small"]) == 0
    assert got == [["--whisper", "small"]]


def test_default_path_leaves_models_ready_so_the_gui_opens_without_the_setup_window(tmp_path):
    def whisper(man, d):
        (d / "whisper-small").mkdir(parents=True)
        (d / "whisper-small" / "model.bin").write_bytes(b"x")
        man["whisper-small"] = {"ok": True}

    def hymt(man, d):
        (d / tiers.HYMT_DIR).mkdir(parents=True)
        (d / tiers.HYMT_DIR / tiers.HYMT_FILE).write_bytes(b"x")
        man[tiers.HYMT_DIR] = {"ok": True}

    assert not tiers.models_ready(tmp_path)
    assert defaults.run(tmp_path, {"whisper-small": whisper, tiers.HYMT_DIR: hymt}, out=lambda s: None) == 0
    assert tiers.models_ready(tmp_path)


def test_progress_lines_are_flushed_for_the_wizard_that_polls_install_log(tmp_path, monkeypatch):
    class Out(io.StringIO):
        flushes = 0

        def flush(self):
            Out.flushes += 1
            super().flush()

    out = Out()
    monkeypatch.setattr("sys.stdout", out)
    defaults.run(tmp_path, _fake([]))
    assert Out.flushes >= 3  # [1/2], [2/2], READY: a redirected stdout is block-buffered without flush


def test_download_size_constant_matches_the_progress_bar_sizes():
    want = sum(tiers.SIZES[k] for k in defaults.DEFAULT_LISTEN_KEYS) // defaults.MB
    assert abs(defaults.DEFAULT_LISTEN_DOWNLOAD_MB - want) <= want * 0.05
