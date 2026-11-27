"""#2035: tests._pwsh.run_pwsh retries a pwsh crash banner exactly once."""
import sys
import textwrap

import pytest

from _pwsh import CRASH_BANNER, PwshCrash, run_pwsh


def _fake(tmp_path, crashes, silent=False):
    """Fake pwsh (a python script): prints the banner on the first `crashes` calls, then OK."""
    counter = tmp_path / "calls"
    script = tmp_path / "fakepwsh.py"
    script.write_text(textwrap.dedent(f"""
        import os, sys
        p = r'{counter}'
        n = int(open(p).read()) if os.path.exists(p) else 0
        open(p, 'w').write(str(n + 1))
        if os.environ.get('PSModuleAnalysisCachePath') is None or os.environ.get('POWERSHELL_UPDATECHECK') != 'Off':
            sys.exit(7)
        if n < {crashes}:
            if not {silent!r}:
                print({CRASH_BANNER!r})
            sys.exit(0)
        print('real output'); sys.exit(3 if 'fail' in sys.argv else 0)
    """))
    return [sys.executable, str(script)], counter


def test_retries_once_after_crash(tmp_path):
    fake, counter = _fake(tmp_path, crashes=1)
    r = run_pwsh(["-Command", "x"], pwsh=fake, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "real output"
    assert counter.read_text() == "2"


def test_second_crash_fails_with_banner(tmp_path):
    fake, counter = _fake(tmp_path, crashes=99)
    with pytest.raises(PwshCrash, match=CRASH_BANNER):
        run_pwsh(["-Command", "x"], pwsh=fake, text=True)
    assert counter.read_text() == "2"


def test_real_script_error_is_not_retried(tmp_path):
    fake, counter = _fake(tmp_path, crashes=0)
    r = run_pwsh(["fail"], pwsh=fake)
    assert r.returncode == 3 and b"real output" in r.stdout
    assert counter.read_text() == "1"


def test_silent_empty_output_is_retried_once(tmp_path):
    fake, counter = _fake(tmp_path, crashes=1, silent=True)
    r = run_pwsh(["-Command", "x"], pwsh=fake, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "real output"
    assert counter.read_text() == "2"


def test_second_silent_crash_fails_with_empty_output(tmp_path):
    fake, counter = _fake(tmp_path, crashes=99, silent=True)
    with pytest.raises(PwshCrash, match="empty output"):
        run_pwsh(["-Command", "x"], pwsh=fake, text=True)
    assert counter.read_text() == "2"
