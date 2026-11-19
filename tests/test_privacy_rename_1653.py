"""#1653: a pure rename (git similarity 100%, 0 changed lines) is not rescanned; anything else still is.
Emails / keys are built by concatenation so this file itself stays clean for the gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import privacy_scan as ps  # noqa: E402

EMAIL = "jane.doe" + "@" + "corp-mail.tw"
KEY = "ghp_" + "Ab1" * 12


class RawGH:
    repo = "o/r"

    def __init__(self, raw: bytes):
        self.raw, self.calls = raw, []

    def get_raw(self, path):
        self.calls.append(path)
        return self.raw


def _rename(**kw):
    f = {"filename": "release/installer/Languages/X.isl", "previous_filename": "installer/Languages/X.isl",
         "status": "renamed", "additions": 0, "deletions": 0, "changes": 0}
    f.update(kw)
    return f


def test_pure_rename_is_not_rescanned():
    gh = RawGH(("; translator " + EMAIL + "\n").encode())
    assert ps.scan_pr_files([_rename()], ps.Combined(), gh, "abc") == []
    assert gh.calls == []  # the moved file is never fetched


def test_pure_rename_still_checks_the_new_file_name():
    hits = ps.scan_pr_files([_rename(filename="docs/" + EMAIL + ".txt")], ps.Combined())
    assert len(hits) == 1 and "(file name)" in hits[0]


@pytest.mark.parametrize("kw", [
    {"additions": 1, "changes": 1},                      # rename + one added line
    {"additions": 3, "deletions": 3, "changes": 6},      # CRLF -> LF rewrites every line
    {"changes": None}, {"additions": None},              # count missing: never assume 0
    {"changes": "0", "additions": "0", "deletions": "0"},  # not an int
    {"previous_filename": None},                          # no old path: not a real rename
    {"status": "copied"}, {"status": "added"}, {"status": "modified"},  # copy-paste / new file
])
def test_anything_but_a_pure_rename_is_scanned_in_full(kw):
    f = _rename(**kw)
    for k in [k for k, v in kw.items() if v is None]:
        del f[k]
    gh = RawGH(("ok\n; translator " + EMAIL + "\nk=" + KEY + "\n").encode())
    hits = ps.scan_pr_files([f], ps.Combined(), gh, "abc")
    assert [h.split(":")[1] for h in hits] == ["2", "3"]


def test_rename_with_edit_scans_the_added_lines_of_the_patch():
    f = _rename(additions=1, changes=1, patch="@@ -1,1 +1,2 @@\n ok\n+key " + KEY + "\n")
    hits = ps.scan_pr_files([f], ps.Combined())
    assert len(hits) == 1 and hits[0].startswith("release/installer/Languages/X.isl:2: key ")
