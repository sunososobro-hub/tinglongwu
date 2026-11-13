# Releasing

Branches: `main` is stable (users and auto-update follow it); `dev` is where daily work happens. `main` is protected: changes arrive by pull request with the `test` CI check green.

To release:

1. Open a PR `dev` -> `main` and wait for CI to pass, then merge it.
2. Tag the merge commit on `main` and push the tag:

   ```
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

3. The `release` workflow checks the tag is on `main`, embeds the tag into `install.sh`, `mac_install.sh` and `install.ps1` (placeholder `__TLWU_REF__`), and creates a **draft** release with them, `SHA256SUMS.txt` and auto-generated notes (a tag with a suffix such as `-beta.1` is marked pre-release). A second job, `windows-installer`, builds the setup wizard with Inno Setup and attaches it twice, as `tinglongwu-setup-X.Y.Z.exe` and as the fixed name `tinglongwu-setup.exe`, adds both hashes to `SHA256SUMS.txt`, then publishes the draft. So a release only becomes `latest` once the wizard the README links is attached; if that job fails, the release stays a draft and the README keeps pointing at the previous one. The wizard is named after `tinglongwu/__init__.py` `__version__`, and the job fails if that does not match the tag. The README install table links `releases/latest/download/{tinglongwu-setup.exe,mac_install.sh,install.sh}`, so it installs from the newest release that is **not** marked pre-release (a pre-release or draft never becomes `latest`). If you replace the wizard by hand, upload it under both names and update `SHA256SUMS.txt`, or the Windows link breaks. The portable `tinglongwu-win64.zip` is built and uploaded by hand under exactly that name (docs/install.md links it as the portable option); list its hash in `SHA256SUMS.txt` too. The raw `main` scripts fall back to `main` when the ref placeholder is not embedded; do not link them for end users.

4. Edit the release notes. Users read the top, developers open the details. Use this order:

   1. `## Download`: one link per OS in the order Windows (`tinglongwu-setup-X.Y.Z.exe`), macOS, Linux, each with a one-line hint for the warning the user will see (SmartScreen, Mac password for BlackHole, run as normal user / apt y/N).
   2. The support table, copied from the README top and updated to what was actually tested for this release. Only mark 🟢 what someone ran on a real machine for this release; keep the README table in sync:

      ```
      | OS | Install | Translate subtitles | Speak (voice) | GPU | CPU-only |
      |---|:-:|:-:|:-:|:-:|:-:|
      | Windows 10/11 | ... |
      | macOS | ... |
      | Linux (Ubuntu 24.04) | ... |

      🟢 tested on a real device · 🟡 install tested in CI only (plus any caveat, e.g. CPU-only latency not measured) · ⚪ not tested yet
      ```

   2b. Right after it, the language table (rows: languages; columns: what the user does). Every light needs evidence, listed in the developer section (step 5): 🟢 real-device or release-gate run, 🟡 CI/eval only or quality still being measured, ⚪ not supported or untested, — same language. Only list languages this tag actually supports. Name each language by its native name plus code (`中文 (zh)`, `English (en)`, `日本語 (ja)`, `Русский (ru)`, `한국어 (ko)`); no flag emoji anywhere, since flags map languages to countries:

      ```
      | Language | Hear it | To Chinese | To English | Speak it (voice) |
      |---|:-:|:-:|:-:|:-:|
      | 中文 (zh) | ... |
      | English (en) | ... |
      ```

   3. Contributors: `Built with help from N people:` and one avatar per login in `.all-contributorsrc` (all-time, reporters included; the list only grows),
      `<a href="https://github.com/LOGIN"><img src="https://github.com/LOGIN.png?size=40" width="40" height="40" alt="LOGIN"></a>`.
      No `@` text here: an @mention notifies the person, so mentioning everyone on every release is spam. @mention only people who are **new** in this release (first contribution or first report since the last release), once, in a `Thanks` line in step 5. GitHub's bottom "Contributors" block then lists only those new people; that is intended.
   4. `## What's new`: at most 3 plain-language bullets about what the user notices. No PR or issue numbers.
   5. `<details><summary>For developers</summary>`: the full changelog with PR/issue links, the evidence for each light in the language table, every asset with its SHA-256, test and gate notes, known issues, a `Thanks` line that @mentions only this release's new contributors (count a PR only if it is in this tag: `git merge-base --is-ancestor <merge-commit> vX.Y.Z`), and a "How to contribute" link to `CONTRIBUTING.md` and the `good first issue` label.

   The README top follows the same order with steps 1-3 only.

If the workflow fails before the release is published, re-run it: the draft only has its files replaced, and the wizard lines in `SHA256SUMS.txt` are rewritten, not appended. The workflow refuses to touch a release that is already published. Upload `tinglongwu-win64.zip` (and add its hash to `SHA256SUMS.txt`) only after the release is published. A tag with any suffix (`v1.2.3-beta.1`) is a pre-release, and `__version__` must carry the same suffix (`1.2.3-beta.1`) for the wizard name check. If the tag itself is wrong (for example not on `main`), delete the draft release (`gh release delete vX.Y.Z`) and the tag (`git push origin :refs/tags/vX.Y.Z`), fix, and tag again.
