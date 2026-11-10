"""#89: nvidia-smi sees a GPU but CUDA 12 cannot use it (driver too old) -> say so, with the fix; never silent."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tinglongwu import device, hwprobe, i18n
from tinglongwu.gui import core


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setattr(i18n, "_override", "en")


def smi(stdout="", exc=None):
    def run(cmd, **kw):
        assert cmd[0] == "nvidia-smi" and "--query-gpu=driver_version" in cmd
        if exc:
            raise exc
        return SimpleNamespace(stdout=stdout)
    return run


def test_driver_version_from_nvidia_smi():
    assert device.driver_version(smi("457.49\n")) == "457.49"
    assert device.driver_version(smi("560.94\n560.94\n")) == "560.94"  # two GPUs: GPU 0
    assert device.driver_version(smi("525.60.13\n")) == "525.60.13"
    assert device.driver_version(smi(exc=FileNotFoundError("nvidia-smi"))) is None
    assert device.driver_version(smi("NVIDIA-SMI has failed because it couldn't communicate\n")) is None
    assert device.driver_version(smi("")) is None


def test_driver_too_old_per_platform():
    assert device.driver_too_old("457.49", "win32")  # the owner's GTX 1050 laptop
    assert device.driver_too_old("527.41", "win32") and not device.driver_too_old("528.33", "win32")
    assert not device.driver_too_old("560.94", "win32")
    assert device.driver_too_old("525.60.11", "linux") and not device.driver_too_old("525.60.13", "linux")
    assert not device.driver_too_old("535.183", "linux")
    assert not device.driver_too_old(None, "win32")  # no NVIDIA GPU: nothing to update


def test_auto_with_old_driver_warns_with_version_and_link():
    msgs = []
    r = device.resolve_device("auto", count=lambda: 0, missing=lambda: [], warn=msgs.append,
                              driver=lambda: "457.49")
    assert r == "cpu" and len(msgs) == 1  # not silent any more
    assert "457.49" in msgs[0] and "too old" in msgs[0] and device.DRIVER_URL in msgs[0]
    assert ".".join(map(str, device.min_driver())) in msgs[0]


def test_auto_with_new_driver_but_no_cuda_names_the_driver_not_no_gpu():
    msgs = []
    r = device.resolve_device("auto", count=lambda: 0, missing=lambda: [], warn=msgs.append,
                              driver=lambda: "999.10")
    assert r == "cpu" and "999.10" in msgs[0] and "too old" not in msgs[0] and "tlwu-setup --gpu" in msgs[0]


def test_no_nvidia_gpu_stays_silent_on_auto():
    msgs = []
    assert device.resolve_device("auto", count=lambda: 0, warn=msgs.append, driver=lambda: None) == "cpu"
    assert msgs == []
    assert device.resolve_device("cuda", count=lambda: 0, warn=msgs.append, driver=lambda: None) == "cpu"
    assert "no CUDA device" in msgs[0]


def test_message_follows_ui_language(monkeypatch):
    monkeypatch.setattr(i18n, "_override", "zh-TW")
    msg = device.gpu_problem("457.49", "win32")
    assert "457.49" in msg and "528.33" in msg and "更新驅動" in msg and device.DRIVER_URL in msg
    assert device.gpu_problem(None) is None


def test_gui_notice_gives_the_real_reason(monkeypatch, tmp_path):
    """run_session (what the GUI calls): the visible notice says 'update the driver', not 'no NVIDIA GPU'."""
    import tinglongwu.asr as asr
    from tinglongwu import cli

    monkeypatch.setattr(device, "cuda_device_count", lambda: 0)
    monkeypatch.setattr(device, "driver_version", lambda: "457.49")

    class Stop(Exception):
        pass

    def boom(*a, **k):
        raise Stop

    monkeypatch.setattr(asr, "FasterWhisperRecognizer", boom)
    notes = []
    with pytest.raises(Stop):
        cli.run_session(cli.parse_args(["--models-dir", str(tmp_path)]), notice=notes.append)
    assert len(notes) == 1 and "CPU mode" in notes[0]
    assert "457.49" in notes[0] and device.DRIVER_URL in notes[0] and "no usable NVIDIA GPU" not in notes[0]
    monkeypatch.setattr(device, "driver_version", lambda: None)  # really no GPU: the old wording
    notes.clear()
    with pytest.raises(Stop):
        cli.run_session(cli.parse_args(["--models-dir", str(tmp_path)]), notice=notes.append)
    assert "no usable NVIDIA GPU" in notes[0]


def test_probe_recommends_cpu_tier_with_update_hint_for_old_driver():
    hw = hwprobe.probe(gpu=lambda: ("GeForce GTX 1650", 4096), ram=lambda: 8192, cores=lambda: 8,
                       driver=lambda: "457.49" if sys.platform == "win32" else "470.82")
    tier = hwprobe.recommend(hw)
    assert (tier.name, tier.device, tier.mt) == ("cpu", "cpu", "opus")
    assert "too old" in tier.reason and device.DRIVER_URL in tier.reason
    ok = hwprobe.recommend(hwprobe.HwInfo("GeForce GTX 1650", 4096, 8192, 8, driver="560.94"))
    assert ok.name == "gpu"
    assert hwprobe.recommend(hwprobe.HwInfo("GeForce GTX 1650", 4096, 8192, 8)).name == "gpu"  # unknown: as before


@pytest.mark.skipif(sys.platform == "win32", reason="shell stub is POSIX-only")
def test_cli_probe_with_old_driver_stub(tmp_path):
    stub = tmp_path / "nvidia-smi"
    stub.write_text("#!/bin/sh\ncase \"$*\" in *driver_version*) echo 470.82;; "
                    "*) echo 'NVIDIA GeForce GTX 1050, 4096';; esac\n")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}", TLWU_UI_LANG="en")
    r = subprocess.run([sys.executable, "-m", "tinglongwu", "--probe"], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "CPU tier" in r.stdout and "470.82" in r.stdout and device.DRIVER_URL in r.stdout


def test_gui_live_draft_defaults_off_without_usable_gpu(tmp_path):
    p = tmp_path / "gui.json"
    assert core.load_settings(p, gpu=lambda: False)["live_draft"] is False  # first run, CPU mode
    assert core.load_settings(p, gpu=lambda: True)["live_draft"] is True
    s = core.load_settings(p, gpu=lambda: False)
    s["live_draft"] = True  # the user turns it on: kept
    core.save_settings(s, p)
    assert core.load_settings(p, gpu=lambda: False)["live_draft"] is True

    def boom():
        raise RuntimeError("probe failed")

    assert core.load_settings(tmp_path / "none.json", gpu=boom)["live_draft"] is True  # unknown: default
