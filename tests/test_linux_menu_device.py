"""#38: the Linux menu must not hardcode one laptop's audio card name as the output device."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "scripts" / "linux_menu.sh"


def test_no_hardcoded_output_device():
    txt = SH.read_text("utf-8")
    assert "Family" not in txt
    assert subprocess.run(["bash", "-n", str(SH)]).returncode == 0


def test_linux_output_choices_use_user_selected_device():
    txt = SH.read_text("utf-8")
    # the device prompt runs for options 2/3 on every platform, not only on macOS
    assert re.search(r'if \[ "\$out" = "2" \] \|\| \[ "\$out" = "3" \]; then', txt)
    assert '--preview-device "$HP" ;;' in txt and '--device "$HP" ;;' in txt


@pytest.mark.parametrize("lang", ["en", "ru", "zh-TW"])
def test_meeting_menu_preview_route_and_enter_hint(lang):
    """#40 (from cook1e0728's fork): meeting mode offers an optional preview device (default off), prints the route and the Enter hint."""
    sh = (ROOT / "scripts/linux_menu.sh").read_text("utf-8")
    i18n = (ROOT / f"scripts/i18n/{lang}.sh").read_text("utf-8")
    for k in ("M_MEET_ASK_PREVIEW", "M_MEET_ROUTE", "M_MEET_OFF", "M_MEET_ENTER_HINT"):
        assert f"{k}=" in i18n and k in sh
    start = sh.index("PREV=()")
    meet = sh[start:sh.index("\nfi", sh.index("exec", start))]
    assert '"${AUTO[@]}" "${PREV[@]}" --listen' in meet and "M_MEET_ROUTE" in meet
    assert 'pv=""' in meet and '[ -n "$pv" ]' in meet   # default = no preview
