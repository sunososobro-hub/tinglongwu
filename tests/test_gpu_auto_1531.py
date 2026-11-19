"""#1531: no force-CPU question in the installer; the device is decided automatically."""
from pathlib import Path

from tlwu_app import tiers

GPU = tiers.Gpu("RTX 3060", 12288, 11000, "551.23")


def test_no_gpu_is_cpu():
    assert tiers.auto_device(None, None) == ("cpu", "")


def test_gpu_selftest_ok_is_cuda():
    assert tiers.auto_device(GPU, True) == ("cuda", "")


def test_gpu_selftest_fail_is_cpu_with_reason():
    dev, line = tiers.auto_device(GPU, False, "CUDA init error")
    assert dev == "cpu" and line == "GPU 無法使用，已改用 CPU：CUDA init error"


def test_user_off_is_cpu_even_if_selftest_ok():
    assert tiers.auto_device(GPU, True, user_off=True) == ("cpu", "")


def test_old_driver_is_cpu():
    old = tiers.Gpu("GTX 760", 2048, 2000, "300.00")
    assert tiers.auto_device(old, True)[0] == "cpu"


def test_installer_has_no_force_cpu_task():
    """Run the script through the task parser: no [Tasks] entry named cpu, no TaskCpu message."""
    text = (Path(__file__).resolve().parent.parent / "release" / "installer" / "tinglongwu.iss").read_text(encoding="utf-8")
    tasks, in_tasks = [], False
    for line in text.splitlines():
        if line.startswith("["):
            in_tasks = line.strip() == "[Tasks]"
        elif in_tasks and line.startswith("Name:"):
            tasks.append(line.split('"')[1])
    assert tasks and "cpu" not in tasks
    assert "TaskCpu" not in text
