"""Guard (#415): CI/app code must never open a visible console window."""
import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = "CREATE_NEW_CONSOLE"


def _tracked_py():
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split("\0")
    return [ROOT / p for p in out if p]


def _uses_flag(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == FORBIDDEN:
            return True
        if isinstance(node, ast.Name) and node.id == FORBIDDEN:
            return True
        if isinstance(node, ast.Constant) and node.value == FORBIDDEN:
            return True
    return False


def test_scanner_detects_flag():
    assert _uses_flag(ast.parse("import subprocess\nx = subprocess.CREATE_NEW_CONSOLE"))
    assert not _uses_flag(ast.parse("x = subprocess.CREATE_NO_WINDOW"))


def test_scanner_detects_getattr_string():
    assert _uses_flag(ast.parse(
        'import subprocess\nf = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)'))
    assert not _uses_flag(ast.parse(
        'import subprocess\nf = getattr(subprocess, "CREATE_NO_WINDOW", 0)'))


def test_no_create_new_console_in_repo():
    bad = [str(p.relative_to(ROOT)) for p in _tracked_py()
           if p != Path(__file__).resolve()  # this guard names the flag as data
           and _uses_flag(ast.parse(p.read_text(encoding="utf-8-sig", errors="replace")))]
    assert not bad, f"{FORBIDDEN} opens a visible window; use CREATE_NO_WINDOW: {bad}"
