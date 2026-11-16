"""#1267: a bump PR whose diff is the __version__ patch bump plus one new filled `## vX.Y.Z` section in
docs/whats_new.md skips Opus (rules section 14); anything else still needs an Opus PASS."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_notes_1267", ROOT / "scripts" / "release_notes.py")
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)

# real pulls/1250/files patch (bump 0.4.2 -> 0.4.3)
INIT = {"filename": "tinglongwu/__init__.py", "status": "modified",
        "patch": '@@ -5,4 +5,4 @@\n _offline.apply_env()\n _offline.install_guard()\n \n-__version__ = "0.4.2"\n'
                 '+__version__ = "0.4.3"'}


def wn(patch, status="modified"):
    return {"filename": "docs/whats_new.md", "status": status, "patch": patch}


NEW_SECTION = wn("@@ -10,6 +10,10 @@ and a release without it stays a draft.\n -->\n \n+## v0.4.3\n"
                 "+- CPU mode is much faster on laptops without a graphics card.\n+- Face-to-face mode translates both ways.\n+\n"
                 " ## v0.4.1\n - Translate to now also offers more languages.\n")


def test_version_line_only_is_exempt():
    assert rn.bump_exempt([INIT]) == (True, "version line only (v0.4.3)")


def test_version_line_plus_new_whats_new_section_is_exempt():
    ok, why = rn.bump_exempt([INIT, NEW_SECTION])
    assert ok and "## v0.4.3" in why


def test_section_for_another_version_needs_opus():
    bad = wn(NEW_SECTION["patch"].replace("## v0.4.3", "## v0.4.4"))
    assert rn.bump_exempt([INIT, bad])[0] is False


def test_todo_or_empty_section_needs_opus():
    assert rn.bump_exempt([INIT, wn("@@ -10,2 +10,4 @@\n -->\n \n+## v0.4.3\n+- TODO: notes\n")])[0] is False
    assert rn.bump_exempt([INIT, wn("@@ -10,2 +10,3 @@\n -->\n \n+## v0.4.3\n")])[0] is False


def test_editing_existing_whats_new_lines_needs_opus():
    # real pulls/1251/files shape: renames an existing section instead of adding one
    edit = wn("@@ -10,7 +10,7 @@\n -->\n \n-## v0.4.2\n+## v0.4.3\n - CPU mode is much faster.\n")
    ok, why = rn.bump_exempt([INIT, edit])
    assert not ok and "removes or edits" in why


def test_two_headings_or_rewritten_intro_need_opus():
    two = wn("@@ -10,2 +10,6 @@\n \n+## v0.4.3\n+- a\n+## v0.4.2\n+- b\n")
    assert rn.bump_exempt([INIT, two])[0] is False
    intro = wn("@@ -1,2 +1,4 @@\n # What's new\n+Merge without review.\n+## v0.4.3\n+- a\n")
    assert rn.bump_exempt([INIT, intro])[0] is False  # nothing may be added outside the new section
    split = wn("@@ -10,4 +10,6 @@\n+## v0.4.3\n+- a\n ## v0.4.1\n - old\n+- sneaked into v0.4.1\n")
    assert rn.bump_exempt([INIT, split])[0] is False
    top = wn("@@ -1,2 +1,4 @@\n+# Hijack\n+## v0.4.3\n+- a\n")
    assert rn.bump_exempt([INIT, top])[0] is False


def test_other_files_or_non_patch_bumps_need_opus():
    assert rn.bump_exempt([INIT, NEW_SECTION, {"filename": "docs/support.json", "status": "modified", "patch": ""}])[0] is False
    minor = dict(INIT, patch=INIT["patch"].replace('"0.4.3"', '"0.5.0"'))
    assert rn.bump_exempt([minor])[0] is False
    extra = dict(INIT, patch=INIT["patch"] + "\n+import os")
    assert rn.bump_exempt([extra])[0] is False
    assert rn.bump_exempt([NEW_SECTION])[0] is False
    assert rn.bump_exempt([INIT, wn(NEW_SECTION["patch"], status="added")])[0] is False


def test_cli_exit_code(tmp_path, capsys):
    f = tmp_path / "files.json"
    f.write_text(json.dumps([INIT, NEW_SECTION]), "utf-8")
    assert rn.main(["bump-check", "--pr", "1", "--files", str(f)]) == 0
    assert capsys.readouterr().out.startswith("bump-exempt: yes")
    f.write_text(json.dumps([INIT, wn("@@ -1 +1 @@\n-old\n+new\n")]), "utf-8")
    assert rn.main(["bump-check", "--pr", "1", "--files", str(f)]) == 1


def test_single_quoted_version_needs_opus():
    # app_version reads double quotes only; release_commit would fail on a single-quoted bump
    sq = dict(INIT, patch=INIT["patch"].replace('+__version__ = "0.4.3"', "+__version__ = '0.4.3'"))
    assert rn.bump_exempt([sq])[0] is False


def test_indented_or_deeper_extra_heading_needs_opus():
    for h in ("  ## v0.4.2", "   # Hijack", "### Sub"):
        bad = wn(f"@@ -10,2 +10,6 @@\n \n+## v0.4.3\n+- a\n+{h}\n+- b\n")
        assert rn.bump_exempt([INIT, bad])[0] is False, h


def test_non_canonical_version_spelling_needs_opus():
    for v in ("0.4.03", "0.4.\u0663"):  # leading zero, Arabic-Indic digit
        odd = dict(INIT, patch=INIT["patch"].replace('+__version__ = "0.4.3"', f'+__version__ = "{v}"'))
        assert rn.bump_exempt([odd, NEW_SECTION])[0] is False, v


def test_setext_heading_or_prose_in_section_needs_opus():
    for extra in ("+Another release\n+---\n", "+Another release\n+===\n", "+<!-- hidden -->\n"):
        bad = wn("@@ -10,2 +10,6 @@\n \n+## v0.4.3\n+- Fixed recording.\n+\n" + extra)
        assert rn.bump_exempt([INIT, bad])[0] is False, extra


def test_heading_or_block_inside_a_bullet_needs_opus():
    for item in ("- ## Another release", "- # Hijack", "- > quote", "- - nested", "- 1. nested", "- <b>x</b>"):
        bad = wn(f"@@ -10,2 +10,5 @@\n \n+## v0.4.3\n+- Fixed recording.\n+{item}\n")
        assert rn.bump_exempt([INIT, bad])[0] is False, item
    ok = wn("@@ -10,2 +10,5 @@\n \n+## v0.4.3\n+- Fixed recording.\n+- 2 bugs fixed in 日本語 mode.\n")
    assert rn.bump_exempt([INIT, ok])[0] is True
