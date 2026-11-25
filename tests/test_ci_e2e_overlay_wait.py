"""#1851: the overlay-stack helper-window wait is 30 s and a failure says why."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ci_e2e  # noqa: E402


def test_wait_is_30_seconds():
    calls = []
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stderr=subprocess.PIPE)
    try:
        win, _ = ci_e2e.wait_helper_window(lambda: calls.append(1) or None, proc, sleep=lambda s: None)
    finally:
        proc.kill()
    assert win is None
    assert len(calls) == 300  # 30 s / 0.1 s (was 100 polls)


def test_failure_reports_exit_code_and_stderr_tail():
    code = "import sys\nfor i in range(30): print('line%d' % i, file=sys.stderr)\nsys.exit(1)"
    proc = subprocess.Popen([sys.executable, "-c", code], stderr=subprocess.PIPE)
    proc.wait()
    win, why = ci_e2e.wait_helper_window(lambda: None, proc, timeout_s=0.3, sleep=lambda s: None)
    assert win is None
    assert "exit code 1" in why
    assert "line29" in why and "line10" in why and "line9\n" not in why  # last 20 lines only


def test_still_running_reported():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stderr=subprocess.PIPE)
    win, why = ci_e2e.wait_helper_window(lambda: None, proc, timeout_s=0.3, sleep=lambda s: None)
    assert win is None and "still running" in why
    proc.kill()


def test_found_window_returned():
    win, why = ci_e2e.wait_helper_window(lambda: 42, None)
    assert (win, why) == (42, "")
