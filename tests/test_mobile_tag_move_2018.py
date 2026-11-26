"""#2018: mobile-latest publish keeps going when GITHUB_TOKEN may not move the tag (workflow-changing commit)."""
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mobile_tag_move.py"
spec = importlib.util.spec_from_file_location("mobile_tag_move", SCRIPT)
mtm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mtm)

OLD, NEW = "63ca42d" + "0" * 33, "5059f3a" + "1" * 33
REFUSAL = "gh: Resource not accessible by integration (HTTP 403)\n"


def fake(responses):
    calls = []

    def run(cmd, **_):
        calls.append(cmd)
        key = "GET" if "-X" not in cmd else cmd[cmd.index("-X") + 1]
        rc, out, err = responses[key]
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return run, calls


def test_refused_patch_keeps_tag_and_succeeds():
    run, calls = fake({"GET": (0, OLD + "\n", ""), "PATCH": (1, "", REFUSAL)})
    state, msg = mtm.move_tag("o/r", "mobile-latest", NEW, run)
    assert state == "kept" and OLD in msg
    assert [c for c in calls if "PATCH" in c]


def test_main_prints_kept_and_exits_zero(capsys):
    run, _ = fake({"GET": (0, OLD, ""), "PATCH": (1, "", REFUSAL)})
    assert mtm.main(["x", "o/r", "mobile-latest", NEW], run) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "tag=kept"
    assert "::warning::" in out.err


def test_allowed_patch_moves():
    run, calls = fake({"GET": (0, OLD, ""), "PATCH": (0, "{}", "")})
    assert mtm.move_tag("o/r", "mobile-latest", NEW, run)[0] == "moved"
    patch = next(c for c in calls if "PATCH" in c)
    assert f"sha={NEW}" in patch and "repos/o/r/git/refs/tags/mobile-latest" in patch


def test_already_at_sha_makes_no_write():
    run, calls = fake({"GET": (0, NEW, "")})
    assert mtm.move_tag("o/r", "mobile-latest", NEW, run)[0] == "moved"
    assert len(calls) == 1


def test_other_patch_error_fails():
    run, _ = fake({"GET": (0, OLD, ""), "PATCH": (1, "", "gh: Unprocessable (HTTP 422)")})
    with pytest.raises(RuntimeError):
        mtm.move_tag("o/r", "mobile-latest", NEW, run)
    assert mtm.main(["x", "o/r", "mobile-latest", NEW], run) == 1


def test_missing_tag_is_created():
    run, calls = fake({"GET": (1, "", "gh: Not Found (HTTP 404)"), "POST": (0, "{}", "")})
    assert mtm.move_tag("o/r", "mobile-latest", NEW, run)[0] == "created"
    assert "ref=refs/tags/mobile-latest" in next(c for c in calls if "POST" in c)


@pytest.mark.skipif(os.name == "nt", reason="fake gh is a shell script")
def test_cli_with_fake_gh_refusal(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$*" in *PATCH*) echo "gh: Resource not accessible by integration (HTTP 403)" >&2; exit 1;; esac\n'
        f"echo {OLD}\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "o/r", "mobile-latest", NEW],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "tag=kept"
