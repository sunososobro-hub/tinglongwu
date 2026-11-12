import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu import cli
from tinglongwu_setup import downloader as dl

ROOT = Path(__file__).resolve().parent.parent
NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text("utf-8")


def _downloaded_ids():
    man = json.loads((ROOT / "tinglongwu_setup" / "manifest.json").read_text("utf-8"))
    ids = [v["repo"] for v in man["models"].values()]
    ids += list(man["argos"])
    ids += [dl.PIPER_REPO, dl.VOSK_TTS_RU, *dl.KOKORO_FILES, "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"]
    ids += [f"ru_RU-{v}-medium" for v in dl.PIPER_RU]
    return ids


@pytest.mark.parametrize("ident", _downloaded_ids())
def test_every_downloaded_model_is_listed(ident):
    assert ident in NOTICES


def test_vosk_marked_unknown_and_opt_in():
    row = next(l for l in NOTICES.splitlines() if dl.VOSK_TTS_RU in l)
    assert "unknown" in row and "opt-in" in row


def test_readme_links_notices():
    assert "(THIRD_PARTY_NOTICES.md)" in (ROOT / "README.md").read_text("utf-8")


def test_version_flag_points_to_notices(capsys):
    with pytest.raises(SystemExit) as e:
        cli.parse_args(["--version"])
    assert e.value.code == 0
    assert "THIRD_PARTY_NOTICES.md" in capsys.readouterr().out
