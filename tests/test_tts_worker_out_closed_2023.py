"""#2023: importing tts_worker dup()s fd 1 into a module-level file; it must be closed at exit (no ResourceWarning)."""
import subprocess
import sys
from pathlib import Path

import tinglongwu.reverse as _rev

REVERSE = Path(_rev.__file__).parent


def test_import_leaves_no_unclosed_file_at_exit():
    code = f"import sys; sys.path.insert(0, {str(REVERSE)!r}); import tts_worker"
    r = subprocess.run([sys.executable, "-W", "always::ResourceWarning", "-c", code],
                       capture_output=True, text=True, timeout=120)
    assert "ResourceWarning" not in r.stderr, r.stderr
