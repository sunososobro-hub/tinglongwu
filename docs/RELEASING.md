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

4. Before tagging, update the single source of the support tables and the contributors block: `docs/support.json` (lights, legend, download hints) and `.all-contributorsrc` (people). Run `python scripts/render_support.py` to rewrite the block between `<!-- SUPPORT:START -->` and `<!-- SUPPORT:END -->` in the three READMEs; CI fails (`tests/test_render_support.py`) if a README block differs from the source. Rules for the source:

   - Device table: only mark 🟢 what someone ran on a real machine (or in the release gate) for this release.
   - Language table: rows are languages by native name plus code (`中文 (zh)`, `English (en)`, `日本語 (ja)`, `Русский (ru)`, `한국어 (ko)`), no flag emoji anywhere (flags map languages to countries). Chinese is one row (speech has no script); Traditional and Simplified are output columns. If a light holds on one OS only, say so in the cell (`🟢 Windows`). Only list languages this tag supports.
   - Contributors: all-time and cumulative (reporters included; the list only grows). "N people" counts humans only; AI tools get their own line, as text links, no logos. The block has no `@` text: an @mention notifies the person, so mentioning everyone on every release is spam.

5. The release workflow writes the draft notes with `python scripts/render_support.py --notes vX.Y.Z` (GitHub's changelog is appended): `## Download`, then exactly the README's block, then `## What's new` with a `TODO:` line. `windows-installer` publishes the draft only when the notes no longer contain `TODO:`; otherwise it leaves a draft and prints the command to publish. Edit the draft:

   - `## What's new`: at most 3 plain-language bullets about what the user notices. No PR or issue numbers.
   - Below it, `<details><summary>For developers</summary>`: the full changelog with PR/issue links, the evidence for each light in the tables, every asset with its SHA-256, test and gate notes, known issues, a `Thanks` line that @mentions only this release's new contributors (count a PR only if it is in this tag: `git merge-base --is-ancestor <merge-commit> vX.Y.Z`), and a "How to contribute" link to `CONTRIBUTING.md` and the `good first issue` label.
   - Do not hand-edit the block between the markers. To change it, edit the source and run `python scripts/render_support.py --edit vX.Y.Z` (it fetches the notes with gh, swaps the block and uploads them; no shell redirection, so no PowerShell encoding issues).
   - Then publish: `gh release edit vX.Y.Z --draft=false`.

If the workflow fails before the release is published, re-run it: the draft only has its files replaced, and the wizard lines in `SHA256SUMS.txt` are rewritten, not appended. The workflow refuses to touch a release that is already published. Upload `tinglongwu-win64.zip` (and add its hash to `SHA256SUMS.txt`) only after the release is published. A tag with any suffix (`v1.2.3-beta.1`) is a pre-release, and `__version__` must carry the same suffix (`1.2.3-beta.1`) for the wizard name check. If the tag itself is wrong (for example not on `main`), delete the draft release (`gh release delete vX.Y.Z`) and the tag (`git push origin :refs/tags/vX.Y.Z`), fix, and tag again.
