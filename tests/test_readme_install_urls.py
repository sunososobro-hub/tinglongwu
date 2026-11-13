"""README one-line installs must point at the latest *release* assets, never at raw `main` scripts.

Release assets have the tag embedded (placeholder __TLWU_REF__), so they install exactly the released version;
raw main scripts fall back to unreleased `main`."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATEST = "https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/"
RAW_MAIN = "https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/"
ASSETS = {"tinglongwu-setup.exe", "mac_install.sh", "install.sh"}
DOCS = ("README.md", "README.zh-CN.md", "README.zh-TW.md", "docs/details.md", "docs/install.md",
        "docs/readme-full.zh-TW.md", "docs/readme-full.zh-CN.md", "PRIVACY.md", "PRIVACY.zh-TW.md")


def _urls(name):
    text = (ROOT / name).read_text("utf-8")
    return re.findall(r"https://[^\s`|\)]+", text)


def test_readmes_install_from_latest_release_assets():
    for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md"):
        got = {u[len(LATEST):] for u in _urls(name) if u.startswith(LATEST)}
        assert ASSETS <= got, name


def test_release_workflow_publishes_the_linked_scripts():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    for asset in ("install.sh", "mac_install.sh"):
        assert f"dist/{asset}" in wf, asset


def test_no_doc_installs_from_raw_main():
    for name in DOCS:
        assert not any(u.startswith(RAW_MAIN) for u in _urls(name)), name


def test_install_table_is_at_the_top_of_the_readme():
    s = (ROOT / "README.md").read_text("utf-8")
    assert s.index(LATEST) < s.index("## ")
    for name in ("README.zh-CN.md", "README.zh-TW.md"):  # right under the first heading (install)
        s = (ROOT / name).read_text("utf-8")
        first = s.index("\n## ")
        assert s.index(LATEST) < s.index("\n## ", first + 1), name


def test_release_workflow_attaches_the_fixed_name_wizard():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    iss = (ROOT / "installer" / "tinglongwu.iss").read_text("utf-8-sig")
    # the job looks for the wizard by the name the .iss gives it, with the version taken from the tag
    assert "OutputBaseFilename=tinglongwu-setup-{#AppVersion}" in iss
    assert 'exe="tinglongwu-setup-${TAG#v}.exe"' in wf
    assert 'cp "$exe" tinglongwu-setup.exe' in wf
    assert "grep -v ' tinglongwu-setup'" in wf  # a re-run must not append duplicate hashes


def test_release_is_a_draft_until_the_wizard_is_attached():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert "--draft" in wf and "--prerelease" in wf
    assert wf.index("--clobber", wf.index("windows-installer:")) < wf.index("--draft=false")


def test_install_rows_are_windows_macos_linux():
    for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md"):
        s = (ROOT / name).read_text("utf-8")
        assert s.index(LATEST + "tinglongwu-setup.exe") < s.index(LATEST + "mac_install.sh") < s.index(LATEST + "install.sh"), name


def test_support_table_and_contributor_avatars_under_install():
    import json
    logins = [c["login"] for c in json.loads((ROOT / ".all-contributorsrc").read_text("utf-8"))["contributors"]]
    for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md"):
        s = (ROOT / name).read_text("utf-8")
        top = s[:s.index("## ", s.index(LATEST))]
        assert "🟢" in top and "🟡" in top and "⚪" in top, name
        for login in logins:
            assert f"https://github.com/{login}.png?size=60" in top, (name, login)


def test_language_table_right_after_the_device_table():
    for name, row in (("README.md", "| 日本語 (ja) |"), ("README.zh-CN.md", "| 日本語 (ja) |"), ("README.zh-TW.md", "| 日本語 (ja) |")):
        s = (ROOT / name).read_text("utf-8")
        assert s.index("| Linux (Ubuntu 24.04) |" if name == "README.md" else "| Linux（Ubuntu 24.04） |") < s.index(row) < s.index("github.com/jchodev.png"), name


def test_no_flag_emoji_in_readmes():  # flags map languages to countries
    import re
    for name in ("README.md", "README.zh-CN.md", "README.zh-TW.md", "docs/RELEASING.md"):
        assert not re.search("[\U0001F1E6-\U0001F1FF]", (ROOT / name).read_text("utf-8")), name
