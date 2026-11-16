"""#1141: the commit-identity fetch retries transient gh API failures."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_pr_commits.sh"


def _run(tmp_path, fail_times):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "count"
    counter.write_text("0")
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        f'n=$(cat {counter}); n=$((n+1)); echo $n > {counter}\n'
        f'if [ "$n" -le {fail_times} ]; then echo "rate limit" >&2; exit 1; fi\n'
        "echo '[[]]'\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", GH_RETRY_SLEEP="0")
    out = tmp_path / "out.json"
    r = subprocess.run(["bash", str(SCRIPT), "o/r", "1", str(out)], env=env, capture_output=True, text=True)
    return r.returncode, int(counter.read_text())


@pytest.mark.skipif(os.name == "nt", reason="bash")
def test_succeeds_after_two_failures(tmp_path):
    assert _run(tmp_path, 2) == (0, 3)


@pytest.mark.skipif(os.name == "nt", reason="bash")
def test_fails_after_three_failures(tmp_path):
    assert _run(tmp_path, 3) == (1, 3)
