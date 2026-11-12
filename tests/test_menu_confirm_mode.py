"""#371: the Linux menu speak flow no longer asks confirm-vs-direct; confirm mode is reachable by --confirm."""
import subprocess
from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent / "scripts" / "tlwu_common.sh"


def _confirm(args="", env=None):
    r = subprocess.run(["bash", "-c", f'. "{COMMON}"; tlwu_confirm_mode {args}'],
                       env={"PATH": "/usr/bin:/bin", **(env or {})})
    return r.returncode == 0


def test_default_is_direct_send():
    assert not _confirm()
    assert not _confirm("--other")


def test_flag_or_env_enables_confirm():
    assert _confirm("--confirm")
    assert _confirm("x --confirm")
    assert _confirm(env={"TLWU_CONFIRM": "1"})

