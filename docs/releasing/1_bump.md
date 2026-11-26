# Releasing: Step 1: bump the version

Part of [RELEASING](../RELEASING.md).

1. **Bump.** The bump role (R-hard) opens one PR to `dev` titled `release: bump __version__ to X.Y.Z` (patch bumps only; minor and major only on the owner's word). It carries `tinglongwu/__init__.py` `__version__` (the single source, #462), a new `## vX.Y.Z` section on top of `docs/whats_new.md` (at most 3 plain-language bullets about what users notice, no PR numbers, no `TODO`). Support updates (`docs/support.json`, `.all-contributorsrc` and the `python scripts/render_support.py` output) land before the bump in their own PR (step 6), so the bump stays exempt. Agents may keep or downgrade lights; only hw-tests evidence justifies a green Windows/NVIDIA light. A bump PR whose diff is only the `__version__` line plus that new `## vX.Y.Z` section merges on green `test` without an Opus PASS (`python scripts/release_notes.py bump-check --pr N` exits 0, #1267); any other file in it needs an Opus PASS.
