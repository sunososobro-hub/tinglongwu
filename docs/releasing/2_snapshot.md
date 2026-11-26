# Releasing: Step 2: snapshot branch and release PR

Part of [RELEASING](../RELEASING.md).

2. **Snapshot and release PR.** When `ci` passes on a push to `dev` (the dev tip) and `__version__` X.Y.Z has no tag, is newer than every tag and its bump commit is not on `main` yet, `release-pr.yml` (`scripts/release_notes.py snapshot`):
   - creates `release/vX.Y.Z` at the dev commit that set `__version__` (once; later dev pushes never move it). If GitHub refuses the workflow token this ref, the job fails and prints `git push origin <sha>:refs/heads/release/vX.Y.Z && gh workflow run release-pr.yml --ref dev -f dry_run=false`, which R-hard runs;
   - dispatches `e2e`, `audio-routing`, `hw-tests` and `install-clean` on `release/vX.Y.Z` (skipping a workflow already green on that commit, and a `hw-tests` run already in progress). Their concurrency groups are per ref, so dev pushes cannot cancel these runs; `ci` and `windows-build` come from the commit's own dev push, which is never cancelled. Snapshot runs restore caches only from `main` or the snapshot ref, so the first e2e / audio-routing run of a snapshot downloads its models;
   - opens the PR `release/vX.Y.Z` -> `main` titled `release: vX.Y.Z`;
   - closes a release PR for another version (the bump was reverted or superseded) and deletes `release/v*` branches whose tag exists or that were superseded. `stale-branches` never deletes `release/v*`.

   Its body has `## What's new` (from `docs/whats_new.md`; a `- TODO:` line until the bump PR wrote the section), the merged PRs since the last tag and thanks to every non-owner, non-bot author of a PR or issue (links, no @mentions). The PR never changes after it is opened; the next version gets its own snapshot. A red check: re-run it, or `gh workflow run <workflow>.yml --ref release/vX.Y.Z`; a real bug is fixed on `dev` and shipped with the next patch bump (the old snapshot PR is then closed as superseded).

   Transition: `release-pr.yml` runs from `main`'s copy, so until one release carries this version to `main` the old copy opens a legacy `dev` -> `main` PR instead. The scripts accept both, and dev stays frozen (`freeze-guard`, `release-fix` only) for that legacy PR alone.
