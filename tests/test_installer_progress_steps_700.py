"""#700: the wizard progress bar must follow install.ps1's "[n/N]" step lines. Runs the real Step function."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PWSH = shutil.which("pwsh")

SCRIPT = r"""
$ast = [System.Management.Automation.Language.Parser]::ParseFile('%s', [ref]$null, [ref]$null)
$fn = $ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq 'Step' }, $true)[0]
function Format-TlwuEstimate($x) { '' }
function Get-TlwuStepEstimate($a, $b, $c) { '' }
Invoke-Expression $fn.Extent.Text
1..8 | ForEach-Object { Step $_ 'zh' 'en' 6>&1 }
"""


@pytest.mark.skipif(PWSH is None, reason="pwsh not installed")
def test_step_lines_match_wizard_format_and_total():
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", SCRIPT % (ROOT / "install.ps1")],
                         capture_output=True, text=True, encoding="utf-8", timeout=120).stdout
    steps = [re.match(r"\[(\d+)/(\d+)\]", ln) for ln in out.splitlines() if ln.startswith("[")]
    assert [int(m.group(1)) for m in steps] == list(range(1, 9))
    assert {m.group(2) for m in steps} == {"8"}   # the wizard now reads N from the line, so any N works
