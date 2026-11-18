"""#1474: decision logic of the weekly dependency audit."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deps_audit import decide, find_vulns

CLEAN = {"dependencies": [{"name": "anyio", "version": "4.16.0", "vulns": []}]}
BAD = {"dependencies": [
    {"name": "anyio", "version": "4.16.0", "vulns": []},
    {"name": "urllib3", "version": "1.0", "vulns": [{"id": "CVE-2099-1", "fix_versions": ["1.1"]}]},
]}


def test_no_cve_posts_nothing():
    assert decide(CLEAN, None) == ("none", None)
    assert decide(CLEAN, 5) == ("none", None)


def test_cve_creates_when_no_open_issue():
    action, body = decide(BAD, None)
    assert action == "create" and "CVE-2099-1" in body and "urllib3 1.0" in body


def test_cve_updates_existing_issue_not_duplicate():
    assert decide(BAD, 42)[0] == "update"


def test_find_vulns_no_fix():
    r = {"dependencies": [{"name": "x", "version": "1", "vulns": [{"id": "GHSA-a"}]}]}
    assert find_vulns(r) == [("x", "1", "GHSA-a", "none")]
