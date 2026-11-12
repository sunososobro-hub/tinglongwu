"""#237: a menu digit ("3") must never reach tlwu/tlwu-setup as a language code ("No Argos package for 3->en")."""
import subprocess
import sys

import pytest


def _check(code):
    return subprocess.run([sys.executable, "-m", "tinglongwu.langs", "--check", code]).returncode


@pytest.mark.parametrize("code", ["auto", "ja", "en", "ru", "zh", "ko"])
def test_valid_codes_pass(code):
    assert _check(code) == 0


@pytest.mark.parametrize("code", ["1", "2", "3", "u", "L", "", "3-en", "xx"])
def test_menu_options_and_junk_rejected(code):
    assert _check(code) == 1
