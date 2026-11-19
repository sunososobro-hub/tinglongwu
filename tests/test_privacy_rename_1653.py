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


BLOB = "b" * 40


class RawGH:
    repo = "o/r"

    def __init__(self, raw: bytes, base_blob=BLOB, fail=False):
        self.raw, self.base_blob, self.fail, self.calls, self.gets = raw, base_blob, fail, [], []

    def get(self, path):
        self.gets.append(path)
        if self.fail:
            raise OSError("api down")
        return None if self.base_blob is None else {"type": "file", "sha": self.base_blob}

    def get_raw(self, path):
        self.calls.append(path)
        return self.raw


def _scan(files, gh, base="base123"):
    return ps.scan_pr_files(files, ps.Combined(), gh, "abc", base)


def _rename(**kw):
    f = {"filename": "release/installer/Languages/X.isl", "previous_filename": "installer/Languages/X.isl",
         "status": "renamed", "additions": 0, "deletions": 0, "changes": 0, "sha": BLOB}
    f.update(kw)
    return f


def test_pure_rename_is_not_rescanned():
    gh = RawGH(("; translator " + EMAIL + "\n").encode())
    assert _scan([_rename()], gh) == []
    assert gh.calls == []  # the moved file is never fetched
    assert gh.gets == ["/repos/o/r/contents/installer/Languages/X.isl?ref=base123"]


SECRET_FILE = ("ok\n; translator " + EMAIL + "\nk=" + KEY + "\n").encode()


@pytest.mark.parametrize("gh_kw,f_kw,base", [
    ({"base_blob": "c" * 40}, {}, "base123"),  # blob differs (e.g. a binary-marked file whose content changed)
    ({}, {"sha": None}, "base123"),            # head blob sha missing
    ({"base_blob": None}, {}, "base123"),      # base lookup gave nothing
    ({"fail": True}, {}, "base123"),           # API error
    ({}, {}, ""),                              # base commit unknown
])
def test_zero_counts_without_matching_blob_are_scanned(gh_kw, f_kw, base):
    f = _rename(**f_kw)
    if f.get("sha") is None:
        del f["sha"]
    gh = RawGH(SECRET_FILE, **gh_kw)
    assert [h.split(":")[1] for h in _scan([f], gh, base)] == ["2", "3"]


def test_cmd_pr_passes_the_base_sha():
    class PRGH(RawGH):
        def get(self, path):
            if path.endswith("/pulls/7"):
                return {"head": {"sha": "abc"}, "base": {"sha": "base123"}}
            return super().get(path)

        def paginate(self, path):
            return [_rename()]

    gh = PRGH(SECRET_FILE)
    assert ps.cmd_pr(gh, 7, rx=ps.Combined()) == 0 and gh.calls == []


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
    gh = RawGH(SECRET_FILE)  # base blob matches: only the other fields decide
    hits = _scan([f], gh)
    assert [h.split(":")[1] for h in hits] == ["2", "3"]


def test_rename_with_edit_scans_the_added_lines_of_the_patch():
    f = _rename(additions=1, changes=1, patch="@@ -1,1 +1,2 @@\n ok\n+key " + KEY + "\n")
    hits = ps.scan_pr_files([f], ps.Combined())
    assert len(hits) == 1 and hits[0].startswith("release/installer/Languages/X.isl:2: key ")
