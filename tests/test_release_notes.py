"""#414: one-click release helpers: scripts/release_notes.py (version, publish plan + owner gates, notes, thanks)
and packaging/release_check.py (zip checks, llama CPU-instruction gate)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rn = load("release_notes", "scripts/release_notes.py")
rc = load("release_check", "packaging/release_check.py")
REPO = "sunososobro-hub/tinglongwu"
SHA, HEAD = "a" * 40, "b" * 40
TAGS = {"v0.2.0": "1" * 40, "v0.3.0-beta.2": "2" * 40, "v0.3.0": "3" * 40, "v0.3.1": "4" * 40, "junk": "5" * 40}
GREEN = [{"id": i, "name": n, "status": "completed", "conclusion": "success", "html_url": f"https://x/{n}"}
         for i, n in enumerate(rn.REQUIRED_CHECKS)]


def pr(title="release: v0.3.2", head="dev", base="main", merged=True, fork=False, num=600):
    return {"number": num, "title": title, "merged_at": "2026-11-13T00:00:00Z" if merged else None,
            "base": {"ref": base}, "head": {"ref": head, "sha": HEAD, "repo": {"full_name": "x/fork" if fork else REPO}}}


def plan(pulls, version="0.3.2", tags=TAGS, runs=GREEN, event="push", ref="main"):
    return rn.plan(event, ref, SHA, version, tags, pulls, REPO, lambda h: runs if h == HEAD else [])


def test_app_version_and_tag_order():
    assert rn.app_version('x = 1\n__version__ = "0.3.1"\n') == "0.3.1"
    assert rn.latest_tag(list(TAGS)) == "v0.3.1"
    assert rn.latest_tag(["v0.3.0-beta.10", "v0.3.0-beta.2"]) == "v0.3.0-beta.10"
    assert rn.latest_tag(["v0.3.0-beta.2", "v0.3.0"]) == "v0.3.0"


def test_merged_release_pr_publishes_with_hw_tests_as_gpu_evidence():
    p = plan([pr()])
    assert (p["publish"], p["build"], p["tag"], p["pr"], p["head"]) == ("true", "true", "v0.3.2", "600", HEAD)
    assert p["hw_run"] == "https://x/hw-tests"


@pytest.mark.parametrize("other", [pr(head="feature"), pr(base="dev"), pr(merged=False), pr(fork=True),
                                   pr(title="docs: roadmap")])
def test_other_pushes_to_main_publish_nothing(other):
    p = plan([other])
    assert p["publish"] == p["build"] == "false"
    assert plan([])["publish"] == "false"


def test_dispatch_is_a_dry_run_and_tag_pushes_do_not_publish():
    p = plan([], event="workflow_dispatch", ref="fix/414")
    assert (p["publish"], p["build"], p["tag"]) == ("false", "true", "v0.3.2")
    assert plan([pr()], event="push", ref="v0.3.2")["publish"] == "false"


@pytest.mark.parametrize("title,version,tags,err", [
    ("release: next", "0.3.2", TAGS, "no valid version"),
    ("release: v0.4.0", "0.3.2", TAGS, "bump __version__"),
    ("release: v0.3.1", "0.3.1", TAGS, "already exists"),
    ("release: v0.3.0-rc.1", "0.3.0-rc.1", TAGS, "not newer"),
])
def test_bad_title_or_tag_fails_the_plan(title, version, tags, err):
    with pytest.raises(SystemExit, match=err):
        plan([pr(title=title)], version=version, tags=tags)


def test_existing_tag_at_the_same_commit_is_a_rerun_unless_published():
    tags = {**TAGS, "v0.3.2": SHA}
    assert plan([pr()], tags=tags)["publish"] == "true"
    done = rn.plan("push", "main", SHA, "0.3.2", tags, [pr()], REPO, lambda h: GREEN, lambda t: t == "v0.3.2")
    assert (done["publish"], done["build"], done["reason"]) == ("false", "false", "release v0.3.2 is already published")


def tag_plan(tag, version, tags=TAGS, runs=GREEN, published=lambda t: False):
    return rn.plan("push", tag, SHA, version, {**tags, tag: SHA}, [], REPO, lambda h: runs if h == SHA else [],
                   published, ref_type="tag")


def test_prerelease_tag_push_publishes_a_prerelease_when_checks_are_green():
    """#112 kept with #414: a vX.Y.Z-suffix tag (on dev or main; release.yml checks the branch) still releases."""
    p = tag_plan("v0.3.2-beta.1", "0.3.2-beta.1")
    assert (p["publish"], p["build"], p["tag"], p["pr"], p["head"]) == ("true", "true", "v0.3.2-beta.1", "", SHA)
    assert p["hw_run"] == "https://x/hw-tests"
    assert tag_plan("v0.3.2-beta.1", "0.3.2-beta.1", published=lambda t: True)["publish"] == "false"


@pytest.mark.parametrize("tag,version,runs,err", [
    ("v0.3.2", "0.3.2", GREEN, "only a pre-release tag"),
    ("v0.3.2-beta.1", "0.3.2", GREEN, "__version__ is 0.3.2"),
    ("v0.3.2-beta.1", "0.3.2-beta.1", GREEN[:3], "hw-tests: no run"),
])
def test_prerelease_tag_push_refuses(tag, version, runs, err):
    with pytest.raises(SystemExit, match=err):
        tag_plan(tag, version, runs=runs)
    with pytest.raises(SystemExit, match="points at"):
        rn.plan("push", "v0.3.2-rc.1", SHA, "0.3.2-rc.1", {"v0.3.2-rc.1": HEAD}, [], REPO, lambda h: GREEN,
                ref_type="tag")


def test_required_checks_must_be_green_on_the_release_pr_head():
    red = [dict(r, conclusion="failure") if r["name"] == "hw-tests" else r for r in GREEN]
    rerun_ok = red + [dict(GREEN[3], id=99)]
    with pytest.raises(SystemExit, match="hw-tests: completed/failure"):
        plan([pr()], runs=red)
    with pytest.raises(SystemExit, match="e2e: no run"):
        plan([pr()], runs=[r for r in GREEN if r["name"] != "e2e"])
    assert plan([pr()], runs=rerun_ok)["publish"] == "true"  # the newest run of each workflow counts
    assert rn.check_problems([dict(GREEN[0], status="in_progress", conclusion=None)], ["ci"]) == [
        "ci: in_progress/None https://x/ci"]


PRS = [{"number": 108, "author": {"login": "cook1e0728"}, "url": "https://g/pull/108"},
       {"number": 402, "author": {"login": "sunososobro-hub"}, "url": ""},
       {"number": 300, "author": {"login": "dependabot[bot]", "is_bot": True}, "url": ""},
       {"number": 301, "author": {"login": "app/github-actions"}, "url": ""}]
ISSUES = [{"number": 34, "author": {"login": "psgo555"}, "url": "https://g/issues/34"},
          {"number": 200, "author": {"login": "cook1e0728"}, "url": "https://g/issues/200"},
          {"number": 1, "author": {"login": "SUNOSOSOBRO-HUB"}, "url": ""}, {"number": 2, "author": None, "url": ""}]


def test_thanks_lists_every_outside_author_with_links_and_no_mentions():
    thanks = rn.contributors(PRS, ISSUES, "sunososobro-hub")
    assert thanks == {"psgo555": [(34, "https://g/issues/34")],
                      "cook1e0728": [(108, "https://g/pull/108"), (200, "https://g/issues/200")]}
    md = rn.thanks_md(thanks)
    assert "- [cook1e0728](https://github.com/cook1e0728): [#108](https://g/pull/108)" in md
    assert "[psgo555](https://github.com/psgo555): [#34](https://g/issues/34)" in md and "@" not in md


def test_pr_body_keeps_the_owners_whats_new_and_warns_on_an_existing_tag():
    thanks = rn.contributors(PRS, ISSUES, "sunososobro-hub")
    body = rn.pr_body("0.3.2", list(TAGS), "v0.3.1", [{"number": 9, "title": "fix x"}], thanks, "", "c" * 40)
    assert "## What's new\n- TODO: add a `## v0.3.2` section to docs/whats_new.md" in body and "- fix x (#9)" in body
    assert rn.whats_new(body) is None  # #589: the placeholder is not a What's new
    assert "Not releasable" not in body and "cook1e0728" in body and "dev ccccccc" in body
    edited = body.replace(rn.todo_whats_new("v0.3.2"), "- Faster subtitles")
    again = rn.pr_body("0.3.2", list(TAGS), "v0.3.1", [], thanks, edited)
    assert rn.whats_new(again) == "- Faster subtitles"
    assert "Not releasable yet" in rn.pr_body("0.3.1", list(TAGS), "v0.3.0", [], {}, "")


def test_release_notes_fill_whats_new_and_state_the_gpu_evidence():
    base = subprocess.run([sys.executable, str(ROOT / "scripts" / "render_support.py"), "--notes", "v0.3.2"],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert "TODO:" in base
    notes = rn.release_notes(base, "## What's new\n- Faster subtitles\n\n## Changes\n", "v0.3.1",
                             "| size | PASS |\n- SHA256: `ab`", "https://x/hw", HEAD, [], {})
    assert "TODO:" not in notes and "## What's new\n- Faster subtitles\n" in notes
    assert "SHA256: `ab`" in notes and "[hw-tests](https://x/hw)" in notes and "CPU path only" in notes
    plain = rn.release_notes(base, "", None, "", "", "", [], {})
    assert "TODO:" in plain and "GPU: not validated" in plain  # #589: no What's new -> the draft gate holds


def test_release_check_size_smoke_and_report():
    assert rc.size_problem(1600 << 20) == "" and "2 GiB" in rc.size_problem(2 * 1024**3)
    assert rc.smoke_verdict(0, "OK   llama_cpp (0.1s)\nsmoke: OK\n") == (True, "OK")
    ok, why = rc.smoke_verdict(0, "OK   llama_cpp (0.1s)\nCPU  llama_cpp: CUDA build not used (x)\nsmoke: OK\n")
    assert ok and "CPU fallback" in why
    assert rc.smoke_verdict(0, "WARN llama_cpp: x\nsmoke: OK\n")[0] is False
    assert rc.smoke_verdict(1, "smoke: OK\nOK   llama_cpp\n")[0] is False
    text = rc.report([("tlwu.exe --smoke", False, "exit code 1")], 1600 << 20, "ab" * 32)
    assert "| tlwu.exe --smoke | FAIL: exit code 1 |" in text and "ab" * 32 in text


def probe_out(**flags):
    return "".join(f"{k} {flags.get(v, 0)}\n" for k, v in rc.BEYOND_AVX2.items())


def test_llama_cpu_gate_rejects_an_avx512_build(tmp_path):
    app = tmp_path / "tlwu"
    for d in ("lib", "lib_cpu"):
        (app / "llama_cpp" / d).mkdir(parents=True)
        (app / "llama_cpp" / d / "ggml-cpu.dll").write_bytes(b"")
    answers = {"lib": probe_out(), "lib_cpu": probe_out()}

    def runner(cmd, **kw):
        assert cmd[1] == "-c" and "ggml_cpu_has_avx512" in cmd[2]
        return subprocess.CompletedProcess(cmd, 0, answers[Path(cmd[3]).parent.name], "")

    assert rc.llama_cpu_check(app, runner) == (True, "lib/ggml-cpu.dll: AVX2 baseline; lib_cpu/ggml-cpu.dll: AVX2 baseline")
    answers["lib"] = probe_out(AVX512=1, AVX512_VNNI=1)  # e.g. the cu125 wheel of #74
    ok, why = rc.llama_cpu_check(app, runner)
    assert not ok and "lib/ggml-cpu.dll: compiled for AVX512, AVX512_VNNI" in why
    assert rc.llama_cpu_check(tmp_path / "empty", runner)[0] is False


def test_probe_verdict_fails_closed():
    assert rc.probe_verdict(0xC000001D, "")[1].startswith("probe exit code 0xc000001d (illegal instruction")
    assert rc.probe_verdict(0, "".join(f"{k} -1\n" for k in rc.BEYOND_AVX2))[0] is False  # nothing answered
    assert rc.probe_verdict(0, "ggml_cpu_has_avx512 0\n") == (True, "AVX2 baseline")


@pytest.mark.skipif(sys.platform != "win32", reason="the probe loads a Windows DLL")
def test_probe_script_runs_against_a_real_dll():
    import ctypes.util

    dll = ctypes.util.find_library("kernel32")  # no ggml exports: every probe answers -1 -> fails closed
    r = subprocess.run([sys.executable, "-c", rc.PROBE % (list(rc.BEYOND_AVX2),), dll], capture_output=True,
                       text=True)
    assert rc.probe_verdict(r.returncode, r.stdout) == (
        False, "no ggml_cpu_has_* export answered; cannot tell the instruction sets")


class _Parsed(Exception):
    pass


def test_release_selftest_argv_parses_with_the_real_setup_and_cli(monkeypatch):
    """The release gate must run commands today's tlwu accepts (#471 removed --engine opus)."""
    import argparse

    from tinglongwu import cli
    from tinglongwu_setup import downloader

    assert rc.SETUP_ARGS[0] == "setup" and "opus" not in rc.SETUP_ARGS + rc.SELFTEST_ARGS
    seen = {}
    real = argparse.ArgumentParser.parse_args

    def capture(self, args=None, namespace=None):
        seen["a"] = real(self, args, namespace)
        raise _Parsed

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(_Parsed):  # stops before any download
        downloader.main([*rc.SETUP_ARGS[1:], "--models-dir", "m"])
    assert seen["a"].whisper == ["tiny"] and seen["a"].hymt is True
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", real)
    a = cli.parse_args([*rc.SELFTEST_ARGS, "--models-dir", "m"])
    assert (a.selftest, a.model, a.engine, a.compute) == (True, "tiny", "hymt", "cpu")


def test_whats_new_keeps_the_owners_subsections():
    body = ("## What's new\nIntro\n\n### Fixes\n- One\n\n### Changes since v0.3.1\n\n- x (#1)\n\n"
            "### Thanks\n\n- y\n---\nfooter\n")
    assert rn.whats_new(body) == "Intro\n\n### Fixes\n- One"


# ---------------------------------------------------------------- #589 auto-release

WN = "# What's new\n<!--\n## v0.3.2\n- example only\n-->\n## v0.3.2\n- Faster subtitles\n\n## v0.3.1\n- Old\n"
OPEN = {"number": 735, "title": "release: v0.3.2", "state": "open", "base": {"ref": "main"},
        "head": {"ref": "dev", "sha": HEAD, "repo": {"full_name": REPO}}}


def auto(enabled="true", throttle="level: 2\nfocus: speak", run=HEAD, version="0.3.2", tags=TAGS, pulls=(OPEN,),
         runs=GREEN, wn=WN, merged=()):
    return rn.auto_plan(enabled, throttle, run, version, tags, list(pulls), REPO, lambda h: runs if h == HEAD else [], wn,
                        list(merged))


def test_whats_new_comes_from_the_version_section_of_the_file():
    assert rn.whats_new_section(WN, "v0.3.2") == "- Faster subtitles"
    assert rn.whats_new_section(WN, "v0.3.1") == "- Old"
    assert rn.whats_new_section(WN, "v0.3.3") is None
    assert rn.whats_new_section("## v0.3.3\n- TODO: write\n", "v0.3.3") is None
    assert rn.whats_new_section("## v0.3.3\n\n## v0.3.2\n- x\n", "v0.3.3") is None
    assert rn.whats_new_section((ROOT / "docs" / "whats_new.md").read_text("utf-8"), "v0.3.4") is None  # example only
    body = rn.pr_body("0.3.2", list(TAGS), "v0.3.1", [], {}, "## What's new\n- PR edit\n", "c" * 40, WN)
    assert rn.whats_new(body) == "- Faster subtitles"  # the file wins over a PR body edit


def test_release_notes_take_the_file_first_and_keep_todo_without_any_source():
    base = "## Download\n\n## What's new\n- TODO: write\n\n## Support\n"
    notes = rn.release_notes(base, "## What's new\n- PR edit\n", None, "", "", "", [], {}, None, WN, "v0.3.2")
    assert "## What's new\n- Faster subtitles\n" in notes and "TODO:" not in notes
    notes = rn.release_notes(base, "## What's new\n- PR edit\n", None, "", "", "", [], {}, None, WN, "v0.3.9")
    assert "## What's new\n- PR edit\n" in notes and "TODO:" not in notes
    assert "- TODO: write" in rn.release_notes(base, "", None, "", "", "", [], {}, None, WN, "v0.3.9")


def test_auto_release_merges_only_a_green_release_pr_at_the_run_commit():
    out = auto()
    assert out == {"merge": "true", "pr": "735", "head": HEAD, "tag": "v0.3.2",
                   "reason": "release PR #735 is green for v0.3.2: merge and publish"}


@pytest.mark.parametrize("kw, why", [
    ({"enabled": ""}, "AUTO_RELEASE"),
    ({"enabled": "false"}, "AUTO_RELEASE"),
    ({"throttle": "level: 0\n"}, "throttle level is 0"),
    ({"throttle": None}, "throttle level is unreadable"),
    ({"throttle": "no level here"}, "throttle level is unreadable"),
    ({"pulls": ()}, "no open dev -> main"),
    ({"pulls": ({**OPEN, "head": {**OPEN["head"], "repo": {"full_name": "x/fork"}}},)}, "no open dev -> main"),
    ({"pulls": ({**OPEN, "title": "docs: x"},)}, "no open dev -> main"),
    ({"run": SHA}, "is not this run's commit"),
    ({"version": "0.3.3"}, "does not name v0.3.3"),
    ({"version": "0.3.1", "pulls": ({**OPEN, "title": "release: v0.3.1"},)}, "not newer than the latest tag"),
    ({"version": "0.2.9", "pulls": ({**OPEN, "title": "release: v0.2.9"},)}, "not newer than the latest tag"),
    ({"runs": GREEN[:3]}, "hw-tests: no run"),
    ({"runs": [*GREEN, {"id": 99, "name": "e2e", "status": "completed", "conclusion": "skipped"}]}, "e2e: completed/skipped"),
    ({"runs": [*GREEN, {"id": 99, "name": "windows-build", "status": "in_progress", "conclusion": None}]}, "windows-build"),
    ({"wn": "## v0.3.1\n- Old\n"}, "no filled `## v0.3.2` section"),
    ({"merged": (pr(),)}, "release PR #600 for v0.3.2 is already merged"),
])
def test_auto_release_refuses(kw, why):
    out = auto(**kw)
    assert out["merge"] == "false" and why in out["reason"], out


def test_auto_release_ignores_unmerged_or_other_version_release_prs():
    assert auto(merged=(pr(merged=False), pr(title="release: v0.3.1"), pr(fork=True)))["merge"] == "true"


def test_dispatch_with_publish_on_main_is_the_release_push():
    pulls = [pr()]
    pushed = plan(pulls)
    out = rn.plan("workflow_dispatch", "main", SHA, "0.3.2", TAGS, pulls, REPO, lambda h: GREEN if h == HEAD else [],
                  publish_input=True)
    assert out == pushed and out["publish"] == "true" and out["tag"] == "v0.3.2" and out["pr"] == "600"
    for ref, ref_type in (("dev", "branch"), ("v0.3.2-rc.1", "tag")):  # publish=true elsewhere is still a dry run
        out = rn.plan("workflow_dispatch", ref, SHA, "0.3.2", TAGS, pulls, REPO, lambda h: GREEN, ref_type=ref_type,
                      publish_input=True)
        assert out["publish"] == "false" and "only on main" in out["reason"]
    with pytest.raises(SystemExit, match="not green"):
        rn.plan("workflow_dispatch", "main", SHA, "0.3.2", TAGS, pulls, REPO, lambda h: [], publish_input=True)
    assert rn.plan("workflow_dispatch", "main", SHA, "0.3.2", TAGS, [], REPO, publish_input=True)["publish"] == "false"


def test_auto_cli_reads_the_kill_switch_before_any_network(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rn, "gh_json", lambda *a: calls.append(a) or ([] if "pulls" in a[1] else {"workflow_runs": []}))
    monkeypatch.setattr(rn, "repo", lambda: REPO)
    monkeypatch.setattr(rn.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "level: 3\n", ""))
    monkeypatch.setattr(rn, "git_tags", lambda: dict(TAGS))
    monkeypatch.setenv("AUTO_RELEASE", "false")
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert rn.main(["auto"]) == 0
    assert "merge=false" in buf.getvalue() and "AUTO_RELEASE" in buf.getvalue()


def _release_yml_notes_argv(values: dict) -> list[str]:
    """#722: the `release_notes.py notes` command exactly as release.yml's "Release notes" step runs it."""
    import os
    import re
    import shlex

    import yaml

    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8"))
    runs = [s["run"] for job in wf["jobs"].values() for s in job.get("steps", []) if "release_notes.py notes" in
            s.get("run", "")]
    assert len(runs) == 1, runs
    cmd = next(c for c in runs[0].replace("\\\n", " ").splitlines() if "release_notes.py notes" in c)
    argv = shlex.split(re.sub(r"\$(\w+)", lambda m: values[m.group(1)], cmd.strip()))
    assert argv[:2] == ["python", "scripts/release_notes.py"]
    return [os.fspath(a) for a in argv[2:]]


def test_release_workflow_notes_list_every_pack_size(tmp_path, monkeypatch):
    pm = load("tlwu_pack_manifest_722", "tinglongwu/pack_manifest.py")
    man = pm.load_manifest(ROOT / "packaging" / "packs.json")  # the checked-in manifest validates
    (tmp_path / "base.md").write_text("## What's new\n- TODO: x\n", "utf-8")
    (tmp_path / "release-check.md").write_text("size OK\n", "utf-8")
    argv = _release_yml_notes_argv({"TAG": "v0.3.9", "PR": "", "HW_RUN": "", "HEAD": "", "DIST": str(tmp_path)})
    assert "--packs-manifest" in argv
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packaging").symlink_to(ROOT / "packaging")  # release.yml runs from the repo root
    monkeypatch.setattr(rn, "repo", lambda: "o/r")
    monkeypatch.setattr(rn, "history", lambda rep, tag, tip="HEAD": (None, [], {}))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert rn.main(argv) == 0
    notes = (tmp_path / "notes.md").read_text("utf-8")
    assert "### Optional packs (downloaded on demand)" in notes
    for name, rec in man["packs"].items():
        assert f"| {name} | {rec['size'] / 1e6:.1f} MB | `{rec['sha256'][:12]}` |" in notes


def test_pack_manifest_matches_the_committed_pins():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_pack_manifest.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    man = load("tlwu_pack_manifest_722b", "tinglongwu/pack_manifest.py").load_manifest(ROOT / "packaging" / "packs.json")
    assert {"ja", "ko", "speak"} <= set(man["packs"])
    assert man["packs"]["ja"]["size"] == 1133080448  # mobile/assets/models_manifest.json, same SHA-256 as the pin
