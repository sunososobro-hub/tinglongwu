"""#40: meeting mode prints where the translated voice goes and how sentences are sent."""
import os
import subprocess
from pathlib import Path

import pytest

COMMON = Path(__file__).resolve().parent.parent / "scripts" / "tlwu_common.sh"


def _banner(lang, mic="tlwu_mic", confirm="0"):
    env = {**os.environ, "TLWU_UI_LANG": lang}
    r = subprocess.run(["bash", "-c", f'. "{COMMON.as_posix()}"; tlwu_load_i18n; tlwu_meeting_banner "$0" "$1"',
                        mic, confirm], env=env, capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.splitlines()


@pytest.mark.parametrize("lang", ["en", "ru", "zh-TW"])
def test_route_line_names_virtual_mic(lang):
    lines = _banner(lang, mic="BlackHole 16ch")
    assert len(lines) == 3 and "BlackHole 16ch" in lines[0]
    assert lines[1].startswith(("voice -> BlackHole 16ch", "голос -> BlackHole 16ch", "聲音 -> BlackHole 16ch"))
    assert "off" not in lines[1] and "выкл" not in lines[1] and "關閉" not in lines[1]


@pytest.mark.parametrize("lang", ["en", "ru", "zh-TW"])
def test_send_hint_matches_confirm_mode(lang):
    direct = _banner(lang, confirm="0")[2]
    confirm = _banner(lang, confirm="1")[2]
    assert "--confirm" in direct and "TLWU_CONFIRM=1" in direct      # direct send: say how to switch
    assert "y+Enter=" in confirm and "Enter=" in confirm.split("y+Enter=", 1)[1]  # confirm: y+Enter sends, Enter discards
    assert direct != confirm
