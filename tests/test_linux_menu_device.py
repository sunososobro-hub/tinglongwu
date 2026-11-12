"""#38: the Linux menu must not hardcode one laptop's audio card name as the output device."""
import re
import subprocess
from pathlib import Path

SH = Path(__file__).resolve().parent.parent / "scripts" / "linux_menu.sh"


def test_no_hardcoded_output_device():
    txt = SH.read_text("utf-8")
    assert "Family" not in txt
    assert subprocess.run(["bash", "-n", str(SH)]).returncode == 0


def test_linux_output_choices_use_user_selected_device():
    txt = SH.read_text("utf-8")
    # the device prompt runs for options 2/3 on every platform, not only on macOS
    assert re.search(r'if \[ "\$out" = "2" \] \|\| \[ "\$out" = "3" \]; then', txt)
    assert '--preview-device "$HP" ;;' in txt and '--device "$HP" ;;' in txt
