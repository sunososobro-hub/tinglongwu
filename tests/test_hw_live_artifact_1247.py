"""#1247 / #1248 review: the hw-live-checks artifact uploads only the mic-check files, never meeting.* / loopback.*.

The upload paths are applied, as globs, to a fake RUNNER_TEMP holding every file hw_live_checks.py writes; only the
mic check's files and the check_*.txt summaries may match.
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parent.parent

WRITTEN = ["live/clips.json", "live/mic_file.jsonl", "live/mic_live.jsonl", "live/mic_live.log",
           "live/mic_report.json", "live/mic.stop", "live/meeting.jsonl", "live/meeting.log",
           "live/loopback.jsonl", "live/loopback.log", "check_348.txt", "check_mic.txt"]
ALLOWED = {"live/mic_file.jsonl", "live/mic_live.jsonl", "live/mic_live.log", "live/mic_report.json",
           "check_348.txt", "check_mic.txt"}


def _upload_paths():
    wf = yaml.safe_load((ROOT / ".github/workflows/hw-tests.yml").read_text(encoding="utf-8"))
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            w = step.get("with") or {}
            if str(step.get("uses", "")).startswith("actions/upload-artifact") and w.get("name") == "hw-live-checks":
                return [ln.strip() for ln in w["path"].splitlines() if ln.strip()]
    raise AssertionError("hw-live-checks upload step not found")


def _uploaded(tmp_path):
    for rel in WRITTEN:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", "utf-8")
    got = set()
    for pat in _upload_paths():
        rel = pat.replace("${{ runner.temp }}/", "")
        got |= {p.relative_to(tmp_path).as_posix() for p in tmp_path.glob(rel)}
    return got


def test_artifact_has_the_mic_check_files(tmp_path):
    assert _uploaded(tmp_path) == ALLOWED


def test_artifact_never_has_meeting_or_loopback_transcripts(tmp_path):
    got = _uploaded(tmp_path)
    assert not {p for p in got if "meeting" in p or "loopback" in p}
