"""#2012: scripts/dispatch_gate.sh decides what to fire from fixtures (a fake `gh`), no LLM."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dispatch_gate.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

FAKE_GH = r'''#!/usr/bin/env bash
F="$FIX"; a="$*"
case "$a" in
  *issues/574*) cat "$F/level" ;;
  *search/issues*model:opus*) if [[ "$a" == *"-label:model:opus"* ]]; then cat "$F/sonnet"; else cat "$F/opus"; fi ;;
  *search/issues*) cat "$F/sonnet" ;;
  *"pulls?state=open"*) cut -d' ' -f1 "$F/prs" 2>/dev/null ;;
  *pulls/*) n=$(echo "$a" | sed -E 's#.*pulls/([0-9]+).*#\1#'); grep "^$n " "$F/prs" | cut -d' ' -f2,3 ;;
  *issues/*/comments*) n=$(echo "$a" | sed -E 's#.*issues/([0-9]+)/.*#\1#'); grep "^$n " "$F/prs" | cut -d' ' -f5 ;;
  *check-runs*) s=$(echo "$a" | sed -E 's#.*commits/([^/]+)/.*#\1#'); grep " $s " "$F/prs" | cut -d' ' -f4 ;;
esac
'''


def run(tmp_path, level="level: 2", sonnet=0, opus=0, prs=()):
    fix = tmp_path / "fix"
    fix.mkdir()
    (fix / "level").write_text(level + "\nfocus: x\n")
    (fix / "sonnet").write_text(f"{sonnet}\n")
    (fix / "opus").write_text(f"{opus}\n")
    (fix / "prs").write_text("".join(p + "\n" for p in prs))
    gh = tmp_path / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    trig = tmp_path / "t.json"
    trig.write_text(json.dumps({"sonnet_workers": ["s1", "s2", "s3"], "opus_workers": ["o1"], "reviewer": "rv"}))
    env = {**os.environ, "GH": str(gh), "FIX": str(fix), "TRIGGERS": str(trig), "R": "o/r"}
    out = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
    return out.stdout.split("\n")[:-1]


def test_level_zero_prints_nothing(tmp_path):
    assert run(tmp_path, level="level: 0", sonnet=3, opus=1) == []


def test_no_ready_prints_nothing(tmp_path):
    assert run(tmp_path) == []


def test_three_ready_three_fire_lines(tmp_path):
    assert run(tmp_path, sonnet=3) == ["FIRE s1", "FIRE s2", "FIRE s3"]


def test_ready_capped_by_listed_triggers_and_opus_separate(tmp_path):
    assert run(tmp_path, sonnet=9, opus=2) == ["FIRE s1", "FIRE s2", "FIRE s3", "FIRE o1"]


def test_level_one_fires_one_sonnet(tmp_path):
    assert run(tmp_path, level="level: 1", sonnet=3) == ["FIRE s1"]


def test_green_clean_pr_over_60_min_fires_reviewer_and_alert_on_3_rejections(tmp_path):
    old = int(time.time()) - 61 * 60
    new = int(time.time()) - 10 * 60
    prs = [f"11 clean sha11 {old} 0", f"12 clean sha12 {new} 3", f"13 dirty sha13 {old} 0"]
    assert run(tmp_path, prs=prs) == ["ALERT 12", "FIRE rv"]


def test_green_pr_under_60_min_no_fire(tmp_path):
    new = int(time.time()) - 10 * 60
    assert run(tmp_path, prs=[f"11 clean sha11 {new} 0"]) == []
