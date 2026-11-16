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


INTRO = "# What's new\n\n<!-- newest first; example:\n## v1.2.3\n- Subtitles appear faster.\n-->\n\n"
HEAD_TEXT = (INTRO + "## v0.4.3\n- CPU mode is much faster on laptops without a graphics card.\n"
             "- Face-to-face mode translates both ways.\n\n## v0.4.1\n- Translate to now also offers more languages.\n")


def rejected(files, reason):
    """Assert the PR is refused for `reason`; the matching head text is supplied, so a missing head file cannot be
    what refuses it."""
    ok, why = rn.bump_exempt(files, HEAD_TEXT)
    assert not ok and reason in why, why


def test_version_line_only_is_exempt():
    assert rn.bump_exempt([INIT]) == (True, "version line only (v0.4.3)")


def test_version_line_plus_new_whats_new_section_is_exempt():
    ok, why = rn.bump_exempt([INIT, NEW_SECTION], HEAD_TEXT)
    assert ok and "## v0.4.3" in why
    assert rn.bump_exempt([INIT, NEW_SECTION])[0] is False  # head file not read -> no exemption


def test_section_for_another_version_needs_opus():
    bad = wn(NEW_SECTION["patch"].replace("## v0.4.3", "## v0.4.4"))
    rejected([INIT, bad], "starts with")


def test_todo_or_empty_section_needs_opus():
    rejected([INIT, wn("@@ -10,2 +10,4 @@\n -->\n \n+## v0.4.3\n+- TODO: notes\n")], "empty or TODO")
    rejected([INIT, wn("@@ -10,2 +10,3 @@\n -->\n \n+## v0.4.3\n")], "empty or TODO")


def test_editing_existing_whats_new_lines_needs_opus():
    # real pulls/1251/files shape: renames an existing section instead of adding one
    edit = wn("@@ -10,7 +10,7 @@\n -->\n \n-## v0.4.2\n+## v0.4.3\n - CPU mode is much faster.\n")
    ok, why = rn.bump_exempt([INIT, edit])
    assert not ok and "removes or edits" in why


def test_two_headings_or_rewritten_intro_need_opus():
    two = wn("@@ -10,2 +10,6 @@\n \n+## v0.4.3\n+- a\n+## v0.4.2\n+- b\n")
    rejected([INIT, two], "exactly one")
    intro = wn("@@ -1,2 +1,4 @@\n # What's new\n+Merge without review.\n+## v0.4.3\n+- a\n")
    rejected([INIT, intro], "starts with")  # nothing may be added outside the new section
    split = wn("@@ -10,4 +10,6 @@\n+## v0.4.3\n+- a\n ## v0.4.1\n - old\n+- sneaked into v0.4.1\n")
    rejected([INIT, split], "starts with")
    top = wn("@@ -1,2 +1,4 @@\n+# Hijack\n+## v0.4.3\n+- a\n")
    rejected([INIT, top], "starts with")


def test_other_files_or_non_patch_bumps_need_opus():
    rejected([INIT, NEW_SECTION, {"filename": "docs/support.json", "status": "modified", "patch": ""}], "other files")
    minor = dict(INIT, patch=INIT["patch"].replace('"0.4.3"', '"0.5.0"'))
    rejected([minor], "not a patch bump")
    extra = dict(INIT, patch=INIT["patch"] + "\n+import os")
    rejected([extra], "more than the __version__ line")
    rejected([NEW_SECTION], "no __version__ change")
    rejected([INIT, wn(NEW_SECTION["patch"], status="added")], "not modified")


def test_cli_exit_code(tmp_path, capsys):
    f = tmp_path / "files.json"
    f.write_text(json.dumps([INIT, NEW_SECTION]), "utf-8")
    w = tmp_path / "whats_new.md"
    w.write_text(HEAD_TEXT, "utf-8")
    assert rn.main(["bump-check", "--pr", "1", "--files", str(f), "--whats-new", str(w)]) == 0
    assert capsys.readouterr().out.startswith("bump-exempt: yes")
    f.write_text(json.dumps([INIT, wn("@@ -1 +1 @@\n-old\n+new\n")]), "utf-8")
    assert rn.main(["bump-check", "--pr", "1", "--files", str(f)]) == 1
    assert "removes or edits" in capsys.readouterr().out


def test_single_quoted_version_needs_opus():
    # app_version reads double quotes only; release_commit would fail on a single-quoted bump
    sq = dict(INIT, patch=INIT["patch"].replace('+__version__ = "0.4.3"', "+__version__ = '0.4.3'"))
    rejected([sq], "more than the __version__ line")


def test_indented_or_deeper_extra_heading_needs_opus():
    for h in ("  ## v0.4.2", "   # Hijack", "### Sub"):
        bad = wn(f"@@ -10,2 +10,6 @@\n \n+## v0.4.3\n+- a\n+{h}\n+- b\n")
        rejected([INIT, bad], "exactly one")


def test_non_canonical_version_spelling_needs_opus():
    for v in ("0.4.03", "0.4.\u0663"):  # leading zero, Arabic-Indic digit
        odd = dict(INIT, patch=INIT["patch"].replace('+__version__ = "0.4.3"', f'+__version__ = "{v}"'))
        rejected([odd, NEW_SECTION], "more than the __version__ line")


def test_setext_heading_or_prose_in_section_needs_opus():
    for extra in ("+Another release\n+---\n", "+Another release\n+===\n", "+<!-- hidden -->\n"):
        bad = wn("@@ -10,2 +10,6 @@\n \n+## v0.4.3\n+- Fixed recording.\n+\n" + extra)
        head = INTRO + "## v0.4.3\n- Fixed recording.\n\n" + extra.replace("+", "")
        ok, why = rn.bump_exempt([INIT, bad], head)
        assert not ok and "bullet lines" in why, (extra, why)


def test_heading_or_block_inside_a_bullet_needs_opus():
    for item in ("- ## Another release", "- # Hijack", "- > quote", "- - nested", "- 1. nested", "- <b>x</b>"):
        bad = wn(f"@@ -10,2 +10,5 @@\n \n+## v0.4.3\n+- Fixed recording.\n+{item}\n")
        head = INTRO + f"## v0.4.3\n- Fixed recording.\n{item}\n"
        ok, why = rn.bump_exempt([INIT, bad], head)
        assert not ok and "bullet lines" in why, (item, why)
    ok = wn("@@ -10,2 +10,5 @@\n \n+## v0.4.3\n+- Fixed recording.\n+- 2 bugs fixed in 日本語 mode.\n")
    head = INTRO + "## v0.4.3\n- Fixed recording.\n- 2 bugs fixed in 日本語 mode.\n"
    assert rn.bump_exempt([INIT, ok], head)[0] is True


def test_section_hidden_in_the_example_comment_needs_opus():
    hidden = wn("@@ -3,3 +3,6 @@\n <!-- newest first; example:\n ## v1.2.3\n - Subtitles appear faster.\n"
                "+## v0.4.3\n+- Fixed recording.\n+\n -->\n")
    head = INTRO.replace("-->", "## v0.4.3\n- Fixed recording.\n\n-->")
    ok, why = rn.bump_exempt([INIT, hidden], head)
    assert not ok and "comment" in why


def test_form_feed_smuggled_statement_needs_opus():
    ff = dict(INIT, patch=INIT["patch"].replace('+__version__ = "0.4.3"', '+__version__ = "0.4.3"\f; print("extra")'))
    rejected([ff], "more than the __version__ line")
    ffw = wn("@@ -10,2 +10,4 @@\n \n+## v0.4.3\n+- Fixed recording.\f## v9.9.9\n")
    rejected([INIT, ffw], "control characters")


def test_live_check_binds_files_and_text_to_one_head():
    heads = iter(["a" * 40, "a" * 40])
    ok, why, sha = rn.bump_check_pr(lambda: next(heads), lambda: [INIT, NEW_SECTION],
                                    lambda h: HEAD_TEXT if h == "a" * 40 else None)
    assert ok and sha == "a" * 40, why
    moved = iter(["a" * 40, "b" * 40])  # a push lands between the file list and the re-check
    ok, why, _ = rn.bump_check_pr(lambda: next(moved), lambda: [INIT, NEW_SECTION], lambda h: HEAD_TEXT)
    assert not ok and "head moved" in why


def test_copy_in_comment_masked_by_existing_visible_section_needs_opus():
    # a visible `## v0.4.3` already exists; the PR adds an identical copy inside the example comment
    hidden = wn("@@ -3,3 +3,6 @@\n <!-- newest first; example:\n ## v1.2.3\n - Subtitles appear faster.\n"
                "+## v0.4.3\n+- Fixed recording.\n+\n -->\n")
    head = INTRO.replace("-->", "## v0.4.3\n- Fixed recording.\n\n-->") + "## v0.4.3\n- Fixed recording.\n"
    ok, why = rn.bump_exempt([INIT, hidden], head)
    assert not ok and "exactly once" in why, why  # two identical blocks: cannot tell which one was added


def test_copy_in_comment_masked_by_a_spaced_visible_heading_needs_opus():
    hidden = wn("@@ -3,3 +3,6 @@\n <!-- newest first; example:\n ## v1.2.3\n - Subtitles appear faster.\n"
                "+## v0.4.3\n+- Fixed recording.\n+\n -->\n")
    for visible in ("##  v0.4.3", "##\tv0.4.3", "## v0.4.3 "):
        head = INTRO.replace("-->", "## v0.4.3\n- Fixed recording.\n\n-->") + f"{visible}\n- Fixed recording.\n"
        ok, why = rn.bump_exempt([INIT, hidden], head)
        assert not ok and "already has" in why, (visible, why)


def test_copy_in_comment_masked_by_an_inline_comment_heading_needs_opus():
    hidden = wn("@@ -3,3 +3,6 @@\n <!-- newest first; example:\n ## v1.2.3\n - Subtitles appear faster.\n"
                "+## v0.4.3\n+- Fixed recording.\n+\n -->\n")
    head = (INTRO.replace("-->", "## v0.4.3\n- Fixed recording.\n\n-->")
            + "## <!-- old -->v0.4.3\n- Fixed recording.\n")
    ok, why = rn.bump_exempt([INIT, hidden], head)
    assert not ok and "already has" in why, why


def test_added_block_may_not_touch_comment_markers():
    bad = wn("@@ -10,2 +10,4 @@\n \n+## v0.4.3\n+- Fixed recording. -->\n")
    rejected([INIT, bad], "HTML comment")
