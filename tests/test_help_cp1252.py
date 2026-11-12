import os
import subprocess
import sys


def test_help_works_on_cp1252_stdout():
    env = dict(os.environ, PYTHONIOENCODING="cp1252:strict")
    r = subprocess.run([sys.executable, "-m", "tinglongwu", "--help"],
                       env=env, capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert b"Speaker N" in r.stdout
