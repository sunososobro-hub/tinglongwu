"""Owner decision #478 (amends #345): a plain vX.Y.Z tag, 0.x included, is published as a normal release so the
newest one stays `latest` (README links use releases/latest/download); only a suffixed tag is a pre-release.

Runs the `pre=(...)` line of the release workflow in bash against sample tags."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rule() -> str:
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    lines = [ln.strip() for ln in wf.splitlines() if ln.strip().startswith("pre=(")]
    assert len(lines) == 1, "release.yml must decide --prerelease in exactly one `pre=(...)` line"
    return lines[0]


def _flags(tag: str) -> str:
    bash = shutil.which("bash")
    assert bash, "bash is required (the release workflow runs in bash)"
    script = _rule() + '\nprintf "%s" "${pre[*]}"'
    out = subprocess.run([bash, "-c", script], env={"TAG": tag, "PATH": ""}, capture_output=True, text=True,
                         check=True)
    return out.stdout


def test_plain_0x_tags_are_not_prerelease():
    for tag in ("v0.3.0", "v0.3.1", "v0.10.2"):
        assert _flags(tag) == "", tag


def test_plain_v1_plus_tags_are_not_prerelease():
    for tag in ("v1.0.0", "v2.3.4"):
        assert _flags(tag) == "", tag


def test_suffixed_tags_are_prerelease():
    for tag in ("v0.4.0-beta.1", "v1.0.0-rc.1", "v1.2.3-alpha"):
        assert _flags(tag) == "--prerelease", tag
