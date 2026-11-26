# Releasing: Step 7: release notes and editing a release

Part of [RELEASING](../RELEASING.md).

7. The notes start from `python scripts/render_support.py --notes vX.Y.Z` (`## Download`, the support tables and contributors block, `## What's new`); `scripts/release_notes.py notes` fills What's new from `docs/whats_new.md` (else the release PR). `windows-installer` publishes only when the notes contain no `TODO:`. To edit a published or draft release later:

   - `## What's new`: at most 3 plain-language bullets about what the user notices. No PR or issue numbers.
   - Below it, `<details><summary>For developers</summary>`: the full changelog with PR/issue links, the evidence for each light in the tables, every asset with its SHA-256, test and gate notes, known issues, a `Thanks` line that @mentions only this release's new contributors (count a PR only if it is in this tag: `git merge-base --is-ancestor <merge-commit> vX.Y.Z`), and a "How to contribute" link to `.github/CONTRIBUTING.md` and the `good first issue` label.
   - Do not hand-edit the block between the markers. To change it, edit the source and run `python scripts/render_support.py --edit vX.Y.Z` (it fetches the notes with gh, swaps the block and uploads them; no shell redirection, so no PowerShell encoding issues).
   - Then publish: `gh release edit vX.Y.Z --draft=false`, but only for a draft that `windows-installer` finished (both `tinglongwu-setup*.exe` files attached). If that job failed, re-run the workflow instead: publishing a draft without the wizard makes it `latest` and breaks the README Windows link.
