"""#958 / #950 part 3: hotfix publish releases only the tested bytes, and only when the kill switch is open."""
import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hotfix_plan.py"
spec = importlib.util.spec_from_file_location("hotfix_plan_publish", SCRIPT)
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)


@pytest.fixture
def tested(tmp_path):
    d = tmp_path / "tested"
    d.mkdir()
    (d / "tinglongwu-win64.zip").write_bytes(b"core zip bytes")
    (d / "tinglongwu-setup-0.3.7.exe").write_bytes(b"wizard bytes")
    return d


def test_record_lists_every_file_with_its_sha256(tested):
    rec = hp.record(str(tested))
    assert "\n" not in rec
    assert hp.parse_sums(rec) == {
        "tinglongwu-setup-0.3.7.exe": hashlib.sha256(b"wizard bytes").hexdigest(),
        "tinglongwu-win64.zip": hashlib.sha256(b"core zip bytes").hexdigest(),
    }


def test_verify_passes_for_the_tested_files(tested):
    rec = hp.record(str(tested))
    assert hp.verify(str(tested), rec) == ["tinglongwu-setup-0.3.7.exe", "tinglongwu-win64.zip"]


def test_verify_fails_when_one_byte_differs(tested):
    rec = hp.record(str(tested))
    (tested / "tinglongwu-win64.zip").write_bytes(b"core zip bytez")
    with pytest.raises(hp.PlanError, match="tinglongwu-win64.zip: sha256"):
        hp.verify(str(tested), rec)


def test_verify_fails_on_a_missing_or_an_extra_file(tested):
    rec = hp.record(str(tested))
    (tested / "tinglongwu-setup-0.3.7.exe").unlink()
    (tested / "evil.dll").write_bytes(b"x")
    with pytest.raises(hp.PlanError) as e:
        hp.verify(str(tested), rec)
    assert "tinglongwu-setup-0.3.7.exe: missing" in str(e.value)
    assert "evil.dll: not in the tested build" in str(e.value)


@pytest.mark.parametrize("bad", ["", "a.zip", "a.zip:1234", "a.zip:" + "0" * 64 + ",a.zip:" + "0" * 64])
def test_malformed_record_is_refused(tested, bad):
    with pytest.raises(hp.PlanError, match="malformed"):
        hp.verify(str(tested), bad)


def test_cli_verify_exit_codes(tested, capsys):
    rec = hp.record(str(tested))
    assert hp.main(["verify", "--dir", str(tested), "--recorded", rec]) == 0
    (tested / "tinglongwu-win64.zip").write_bytes(b"rebuilt")
    assert hp.main(["verify", "--dir", str(tested), "--recorded", rec]) == 1
    assert "not the tested build" in capsys.readouterr().err


def test_cli_record_writes_github_output(tested, tmp_path, monkeypatch):
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert hp.main(["record", "--dir", str(tested)]) == 0
    line = out.read_text("utf-8").strip()
    assert line.startswith("sums=") and hp.parse_sums(line[5:])


@pytest.mark.parametrize("enabled,body,ok", [
    ("true", "level: 2\nfocus: speak", True),
    ("true", "level: 1", True),
    ("true", "level: 0\n", False),       # throttle off
    ("false", "level: 4", False),        # AUTO_RELEASE off
    ("", "level: 4", False),             # variable missing
    ("true", None, False),               # throttle issue unreadable
    ("true", "focus: speak\nlevel: 3", False),  # level must be the first line
])
def test_killswitch(enabled, body, ok):
    if ok:
        hp.killswitch(enabled, body)
    else:
        with pytest.raises(hp.PlanError, match="kill switch"):
            hp.killswitch(enabled, body)


def test_cli_killswitch_reads_the_throttle_file(tmp_path):
    f = tmp_path / "t.md"
    f.write_text("level: 3\n", "utf-8")
    assert hp.main(["killswitch", "--enabled", "true", "--throttle-file", str(f)]) == 0
    assert hp.main(["killswitch", "--enabled", "true", "--throttle-file", str(tmp_path / "missing.md")]) == 1
    f.write_text("level: 0\n", "utf-8")
    assert hp.main(["killswitch", "--enabled", "true", "--throttle-file", str(f)]) == 1


def test_notes_name_base_fix_and_issue(tmp_path):
    out = tmp_path / "notes.md"
    assert hp.main(["notes", "--tag", "v0.3.7", "--base", "v0.3.6", "--fix", "a" * 40, "--issue", "12",
                    "--pr", "34", "--out", str(out)]) == 0
    text = out.read_text("utf-8")
    assert text.startswith("Hotfix v0.3.7: v0.3.6 plus one urgent fix for #12 (PR #34), commit aaaaaaaaaaaa.")


def _steps(job):
    return [s.get("name", s.get("uses", "")) for s in job["steps"]]


def test_workflow_publish_order():
    """Only the publish job can write, and it checks kill switch + sha256 before it pushes, tags or releases."""
    wf = yaml.safe_load((ROOT / ".github/workflows/hotfix.yml").read_text("utf-8"))
    jobs = wf["jobs"]
    writers = [n for n, j in jobs.items() if (j.get("permissions") or {}).get("contents") == "write"]
    assert writers == ["publish"]
    pub = jobs["publish"]
    assert set(pub["needs"]) == {"plan", "test", "windows", "summary"}
    assert "inputs.publish" in pub["if"] and "needs.summary.result == 'success'" in pub["if"]
    names = _steps(pub)
    idx = {k: next(i for i, n in enumerate(names) if n.startswith(k))
           for k in ("Kill switch", "Fresh plan", "The files to publish", "Push hotfix", "Create the Release",
                     "Open the back-merge")}
    assert idx["Kill switch"] < idx["Push hotfix"] and idx["Fresh plan"] < idx["Push hotfix"]
    assert idx["The files to publish"] < idx["Push hotfix"] < idx["Create the Release"] < idx["Open the back-merge"]
    assert "sums" in jobs["windows"]["outputs"]
