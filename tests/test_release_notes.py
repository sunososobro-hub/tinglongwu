"""#414: release automation helpers (scripts/release_notes.py): versions, the release plan, the thanks list."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)

TAGS = ["v0.1.0", "v0.2.0", "v0.3.0-beta.1", "v0.3.0-beta.2", "junk", "v0.3.0-beta.10"]
OWNER = "sunososobro-hub"


def a(login, bot=False):
    return {"login": login, "is_bot": bot}


def test_next_version():
    assert rn.latest_tag(TAGS) == "v0.3.0-beta.10"
    assert rn.next_version(TAGS) == "v0.3.0-beta.11"
    assert rn.next_version(["v0.3.0-beta.2", "v0.3.0"]) == "v0.3.1"
    assert rn.next_version([]) == "v0.1.0"
    assert rn.next_version(["v1.2.3-rc"]) == "v1.2.3"


def test_release_version_title_override_and_guards():
    assert rn.release_version("release: v0.3.0", TAGS) == "v0.3.0"
    assert rn.release_version("release: whatever", TAGS) == "v0.3.0-beta.11"
    with pytest.raises(SystemExit, match="already exists"):
        rn.release_version("release: v0.2.0", TAGS)
    with pytest.raises(SystemExit, match="not newer"):
        rn.release_version("release: v0.2.5", TAGS)


def pr(num=500, title="release: v0.3.0", head="dev", base="main", merged=True, labels=()):
    return {"number": num, "title": title, "head": {"ref": head}, "base": {"ref": base},
            "merged_at": "2026-11-12T00:00:00Z" if merged else None, "labels": [{"name": n} for n in labels]}


def test_plan_publishes_only_a_merged_release_pr():
    p = rn.plan("push", "branch", "main", TAGS, [pr()])
    assert (p["publish"], p["tag"], p["pr"], p["gpu_local"]) == ("true", "v0.3.0", "500", "false")
    assert rn.plan("push", "branch", "main", TAGS, [pr(labels=[rn.GPU_LABEL])])["gpu_local"] == "true"
    for other in (pr(head="meta/x"), pr(title="meta: dashboard"), pr(merged=False), pr(base="dev")):
        assert rn.plan("push", "branch", "main", TAGS, [other])["publish"] == "false"
    assert rn.plan("push", "branch", "main", TAGS, [])["publish"] == "false"


def test_plan_dispatch_is_a_dry_run_and_tags_are_checked():
    p = rn.plan("workflow_dispatch", "branch", "cd/414", TAGS, [])
    assert p["publish"] == "false" and p["tag"].endswith("-dryrun")
    assert rn.plan("push", "tag", "v0.4.0", TAGS, [])["publish"] == "true"
    with pytest.raises(SystemExit, match="bad tag"):
        rn.plan("push", "tag", "vNext", TAGS, [])


def test_contributors_thank_every_outside_author():
    prs = [{"number": 108, "author": a("cook1e0728")}, {"number": 402, "author": a(OWNER)},
           {"number": 300, "author": a("dependabot[bot]", bot=True)}, {"number": 301, "author": a("app/github-actions")}]
    issues = [{"number": 34, "author": a("psgo555")}, {"number": 200, "author": a("cook1e0728")},
              {"number": 1, "author": a(OWNER.upper())}, {"number": 2, "author": None}]
    assert rn.contributors(prs, issues, OWNER) == {"psgo555": ["#34 (issue)"],
                                                   "cook1e0728": ["#108 (PR)", "#200 (issue)"]}


def test_render_has_thanks_and_the_gpu_statement():
    thanks = {"psgo555": ["#34 (issue)"], "cook1e0728": ["#108 (PR)"]}
    text = rn.render("v0.3.0", "v0.3.0-beta.2", [{"number": 9, "title": "fix x"}], thanks,
                     "zip: 1600 MB", rn.gpu_line(False))
    assert "- fix x (#9)" in text and "@psgo555: #34 (issue)" in text and "@cook1e0728: #108 (PR)" in text
    assert "GPU: not validated" in text
    assert "validated locally" in rn.gpu_line(True, "500") and "#500" in rn.gpu_line(True, "500")


spec = importlib.util.spec_from_file_location("release_check", ROOT / "packaging" / "release_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def test_release_check_size_limit_and_smoke_verdict():
    assert rc.size_problem(1600 * 1024**2) == ""
    assert "2 GiB" in rc.size_problem(2 * 1024**3)
    assert rc.smoke_verdict(0, "OK   llama_cpp (0.1s)\nsmoke: OK\n") == (True, "OK")
    ok, why = rc.smoke_verdict(0, "OK   llama_cpp (0.1s)\nCPU  llama_cpp: CUDA build not used (x)\nsmoke: OK\n")
    assert ok and "CPU fallback" in why
    assert rc.smoke_verdict(0, "WARN llama_cpp: x\nsmoke: OK\n")[0] is False
    assert rc.smoke_verdict(1, "smoke: OK\nOK   llama_cpp\n")[0] is False
    text = rc.report([("size < 2 GiB", True, "1600 MB"), ("tlwu.exe --smoke", False, "exit code 1")], 1600 << 20, "ab" * 32)
    assert "| tlwu.exe --smoke | FAIL: exit code 1 |" in text and "ab" * 32 in text
