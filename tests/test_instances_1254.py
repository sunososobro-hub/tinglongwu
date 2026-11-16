"""#1254: a second tlwu instance in CPU mode sees the first one's threads, takes only what is left and warns once
with the other instance's PID. Real processes, real OS locks."""
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from tinglongwu import cli, instances

HOLDER = textwrap.dedent("""
    import sys
    from tinglongwu import instances
    assert instances.register(int(sys.argv[1]))
    print("ready", flush=True)
    sys.stdin.read()  # hold the registration until the test closes stdin
""")


@pytest.fixture
def first_instance(tmp_path):
    """Another process registered as a CPU-mode tlwu using 12 threads (the field case: asr 6 + mt 6 on 12 CPUs)."""
    procs = []

    def start(threads):
        p = subprocess.Popen([sys.executable, "-c", HOLDER, str(threads)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env=os.environ.copy())
        assert p.stdout.readline().strip() == "ready"
        procs.append(p)
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.stdin.close()
            p.wait(timeout=20)


def _args():
    return SimpleNamespace(model="small", quality="auto", min_words=0, min_words_explicit=True)


def test_second_instance_shares_the_cpu_and_names_the_first_pid(first_instance, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    other = first_instance(8)
    a = _args()
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=12, ram_gb=16)
    err = capsys.readouterr().err
    assert f"PID {other.pid} (8 threads)" in err
    from tinglongwu import threads

    mt = threads.current()["mt"]
    assert a.cpu_threads + mt == 4  # 12 CPUs - 8 taken by the other instance
    assert 8 + a.cpu_threads + mt <= 12
    assert "oversubscribed" not in err
    # and the first instance would see this one too
    assert [o.threads for o in instances.others({"TLWU_INSTANCE_DIR": os.environ["TLWU_INSTANCE_DIR"]})] == [8]


def test_full_cpu_taken_still_runs_and_says_oversubscribed(first_instance, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    other = first_instance(12)
    a = _args()
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=12, ram_gb=16)
    err = capsys.readouterr().err
    from tinglongwu import threads

    assert a.cpu_threads + threads.current()["mt"] == instances.MIN_THREADS
    assert f"PID {other.pid}" in err and "oversubscribed" in err


def test_transcript_only_registers_asr_threads_only(tmp_path, monkeypatch):
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    a = _args()
    a.transcript_only = True
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=12, ram_gb=16)
    code = "from tinglongwu import instances; print([o.threads for o in instances.others()])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ.copy())
    assert out.stdout.strip() == f"[{a.cpu_threads}]"


def test_ended_instance_is_not_counted_and_its_files_go(first_instance, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    other = first_instance(8)
    other.kill()  # a crash: no clean-up ran, the OS dropped the lock
    other.wait(timeout=20)
    a = _args()
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=12, ram_gb=16)
    assert "another tlwu" not in capsys.readouterr().err
    d = os.environ["TLWU_INSTANCE_DIR"]
    assert not os.path.exists(os.path.join(d, f"{other.pid}.json"))


def test_alone_no_warning_and_release_frees_the_threads(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    a = _args()
    cli.apply_cpu_mode(a, "cpu", tmp_path, explicit_model=True, cores=12, ram_gb=16)
    assert "another tlwu" not in capsys.readouterr().err
    code = "from tinglongwu import instances; print(len(instances.others()))"
    seen = lambda: subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,  # noqa: E731
                                  env=os.environ.copy()).stdout.strip()
    assert seen() == "1"
    instances.release()
    assert seen() == "0"


def test_gpu_run_registers_nothing(tmp_path):
    a = SimpleNamespace(model="large-v3", quality="auto")
    cli.apply_cpu_mode(a, "cuda", tmp_path, explicit_model=False)
    d = os.environ["TLWU_INSTANCE_DIR"]
    assert not os.path.isdir(d) or not os.listdir(d)


RACER = textwrap.dedent("""
    import sys, time
    from tinglongwu import instances
    with instances.allocating():
        seen = instances.others()
        time.sleep(0.5)  # both processes are inside discovery/planning at once without the lock
        instances.register(6)
    print(len(seen), flush=True)
    sys.stdin.read()
""")


def test_two_instances_starting_together_do_not_both_plan_for_the_whole_cpu():
    procs = [subprocess.Popen([sys.executable, "-c", RACER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, env=os.environ.copy()) for _ in range(2)]
    try:
        seen = sorted(int(p.stdout.readline()) for p in procs)
    finally:
        for p in procs:
            p.stdin.close()
            p.wait(timeout=20)
    assert seen == [0, 1]  # the second one waited for the first and counted it


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership / permission bits")
def test_registry_dir_writable_by_others_is_not_used(tmp_path, monkeypatch):
    d = tmp_path / "shared"
    d.mkdir()
    d.chmod(0o777)
    monkeypatch.setenv("TLWU_INSTANCE_DIR", str(d))
    assert instances.register(4) is False
    assert os.listdir(d) == []


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="needs O_NOFOLLOW")
def test_register_does_not_write_through_a_planted_symlink(tmp_path, monkeypatch):
    d = tmp_path / "reg"
    d.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    os.symlink(victim, d / f"{os.getpid()}.json.tmp")
    monkeypatch.setenv("TLWU_INSTANCE_DIR", str(d))
    assert instances.register(4)
    assert victim.read_text() == "keep"


def test_failed_start_releases_the_registration(tmp_path, monkeypatch):
    """Loading fails after apply_cpu_mode registered (bad --glossary): the threads are free again."""
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    monkeypatch.setattr("tinglongwu.minspec.check_avx2", lambda force=False: None)
    monkeypatch.setattr("tinglongwu.device.resolve_device", lambda compute, **kw: "cpu")
    a = cli.parse_args(["--device", "cpu", "--glossary", str(tmp_path / "missing.txt"),
                                       "--models-dir", str(tmp_path)])
    with pytest.raises(RuntimeError, match="glossary"):
        cli.run_session(a)
    d = os.environ["TLWU_INSTANCE_DIR"]
    assert not os.path.exists(os.path.join(d, f"{os.getpid()}.json"))


def test_cuda_fallback_to_cpu_registers_and_shares(first_instance, monkeypatch, capsys):
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    other = first_instance(8)
    assert cli.cpu_fallback_mt("cuda", "cpu", cores=12, asr_threads=2)
    from tinglongwu import threads

    assert 2 + threads.current()["mt"] == 4  # MT takes what the other instance and the loaded Whisper leave
    assert f"PID {other.pid}" in capsys.readouterr().err
    code = "from tinglongwu import instances; print(sorted(o.threads for o in instances.others()))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ.copy())
    assert out.stdout.strip() == "[4, 8]"
