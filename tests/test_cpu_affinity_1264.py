"""#1264: tlwu plans CPU threads for the CPUs it may run on, not every CPU of the machine. The 2-CPU meeting
replay pins tlwu to 2 of 4 CPUs (bench-cpu-ab run 43919414629) and tlwu logged "CPU mode threads: 4 CPUs ->
asr=2 mt=2/2", i.e. 4 busy threads on 2 cores. The same happens to a user in a container cpuset or with
process affinity set."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tinglongwu import cli, hwprobe, threads


def _pin(monkeypatch, total, allowed):
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: total)
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(allowed)), raising=False)
    monkeypatch.setattr(threads, "_win_affinity_count", lambda: allowed)  # same mask on a Windows test host


def test_cpu_count_is_the_affinity_mask_not_the_machine(monkeypatch):
    _pin(monkeypatch, 4, 2)
    assert threads.cpu_count() == 2
    assert hwprobe.probe(gpu=lambda: (None, None), ram=lambda: 8192).cores == 2


def test_cpu_count_falls_back_to_os_cpu_count(monkeypatch):
    monkeypatch.delattr(os, "process_cpu_count", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 6)
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)  # Windows / macOS before 3.13
    monkeypatch.setattr(threads, "_win_affinity_count", lambda: None)  # mask unreadable on a Windows test host
    assert threads.cpu_count() == 6
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert threads.cpu_count() == 4


def test_cpu_mode_plans_threads_for_the_pinned_cpus(monkeypatch, tmp_path, capsys):
    _pin(monkeypatch, 4, 2)
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    monkeypatch.delenv("TLWU_BOTH", raising=False)
    monkeypatch.setattr(threads, "_role", "listen")
    monkeypatch.setattr("tinglongwu.models.whisper_installed", lambda mdir=None: {"small"})
    monkeypatch.setattr("tinglongwu.instances.others", lambda: [])
    monkeypatch.setattr("tinglongwu.instances.register", lambda n: None)
    a = SimpleNamespace(model="small", quality="auto", min_words=0, min_words_explicit=True)
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, ram_gb=16)
    assert "CPU mode threads: 2 CPUs" in capsys.readouterr().err
    assert a.cpu_threads + threads.current()["mt"] <= 2


@pytest.mark.skipif(not hasattr(os, "sched_setaffinity"), reason="needs Linux process affinity")
def test_a_really_pinned_process_sees_one_cpu():
    code = ("import os; os.sched_setaffinity(0, {sorted(os.sched_getaffinity(0))[0]}); "
            "from tinglongwu import hwprobe; print(hwprobe.probe(gpu=lambda: (None, None), ram=lambda: 1).cores)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "1"


class _Kernel32:
    """Fake kernel32: GetProcessAffinityMask fills the process / system masks it is given."""

    def __init__(self, proc_mask, system_mask=0b1111, ok=True):
        self.masks, self.ok = (proc_mask, system_mask), ok

    def GetCurrentProcess(self):
        return -1

    def GetProcessAffinityMask(self, handle, proc, system):
        proc._obj.value, system._obj.value = self.masks
        return self.ok


def test_windows_affinity_mask_is_counted(monkeypatch):
    # Python's os.process_cpu_count() ignores the Windows affinity mask (no sched_getaffinity there), so a process
    # pinned to 2 of 4 CPUs (Task Manager "Set affinity", `start /affinity 3`) planned 4 CPUs before #1264 r3.
    assert threads._win_affinity_count(_Kernel32(0b0101)) == 2
    assert threads._win_affinity_count(_Kernel32(0b0101, ok=False)) is None
    assert threads._win_affinity_count(_Kernel32(0)) is None
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    monkeypatch.setattr(os, "process_cpu_count", lambda: 4, raising=False)  # what Windows Python reports
    monkeypatch.setattr(threads, "_win_affinity_count", lambda: 2)
    assert threads.cpu_count() == 2
    monkeypatch.setattr(threads, "_win_affinity_count", lambda: None)  # mask unreadable: the machine count
    assert threads.cpu_count() == 4


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows process affinity")
def test_a_really_pinned_windows_process_sees_one_cpu():
    code = ("import ctypes; k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; "
            "assert k.SetProcessAffinityMask(ctypes.c_void_p(k.GetCurrentProcess()), ctypes.c_size_t(1)); "
            "from tinglongwu import hwprobe; print(hwprobe.probe(gpu=lambda: (None, None), ram=lambda: 1).cores)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "1"
