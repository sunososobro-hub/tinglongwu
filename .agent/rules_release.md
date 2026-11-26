# tlwu agent rules: release lane

Read after `.agent/tlwu_rules.md` (same precedence, shared section numbers) by R-hard, R-merge, S-merge and S-hard. Workers need only the core file. Shared merge definitions (allowed review authors, Opus PASS, dev Green, Ready, g1-g7, protected paths) are in core section 14. Why each rule exists: `docs/DECISIONS.md`.

## 11. Docs fast lane to main
- Docs-only changes may reach `main` without a release. Scope: the PR changes only `README*.md` and `docs/**/*.md` (not `docs/support.json`, no code, scripts, `.github/`, `.agent/`, packaging, `pyproject.toml`, `tinglongwu/__init__.py`).
- Order: the change merges to `dev` first. Then a merger opens `main <- docs/fastlane-N` carrying the same file contents (cherry-pick of the dev commit), title `docs(fastlane): ...`, body `Refs #N` + `tlwu-usage` line.
- Gate: `test` green on that PR and the diff inside the scope above (a merger checks `pulls/N/files`). Merge by rebase. No tag, no release; `release.yml` publishes nothing for a non-release push.
- Sync back: right after the fast-lane merge, merge `main` into `dev` (PR `dev <- main`, merge commit, auto-merge on green).
- Skip while a `release.yml` run on `main` is in progress; retry after it finishes. An open `release/vX.Y.Z` PR is no reason to wait.
- This is the only way an agent changes `main` besides the release PR; it never carries `.github/` or code changes.

## 14. Auto-release
When every release gate is green on the release PR, it releases with no human click; every merged dev change becomes a user-facing release, so these rules are hard stops for every merger and are stricter than core section 3. The owner never merges PRs, including the release PR.

### Release PR green and per-platform lanes
- Release PR green: every REQUIRED_CHECKS workflow (`scripts/release_notes.py`: `ci`, `e2e`, `windows-build`, `hw-tests`, `audio-routing`, `install-clean`) `success` on the snapshot head (`skipped` does not count). When the `hw-tests` run for that head has not started within `HW_RUNNER_WAIT_MIN` = 30 minutes, `hw-tests` is deferred: the other five `success` are enough, the release notes say "hardware checks deferred", and a later `hw-tests` failure on that commit or the next dev push opens a P0 issue `[release] deferred hw-tests failed on vX.Y.Z`. A started `hw-tests` run is never deferred.
- Per-platform lanes: the release gate is the shared checks (ci `test` + offline suite) plus the Windows jobs. In `e2e` and `install-clean` a failed macOS / Linux job (by job name, `scripts/release_notes.py` `job_platform`) does not block the tag, the Windows exe or Windows auto-update. macOS / Linux ship by `release.yml` moving their marker tag (`mac-ok` / `linux-ok`) to the release tag only when all of that platform's jobs passed on the snapshot head; otherwise they stay on their last good version. `mac_install.sh` / `install.sh` and the menu's auto-update install that marker, not `main`. The release notes list per platform `shipped vX.Y.Z` or `skipped (not verified), stays on vA.B.C`. Only `release.yml` moves marker tags. Android lane unchanged.

### Release PR
- The release PR (`release/vX.Y.Z -> main`, title `release: vX.Y.Z`) is merged only by `auto-release.yml`. Mergers and workers never merge, approve, close or edit it.

### Version bumps
- Only the bump role (R-hard) changes `__version__` in `tinglongwu/__init__.py`, with one PR titled `release: bump __version__ to X.Y.Z`, gate evidence in the body, an issue `[release] bump __version__ to X.Y.Z` and head `fix/<that issue>`.
- Cadence: every R-hard wake opens a patch bump when all hold: the tag `v<dev __version__>` exists, or (red-snapshot recovery, #1893) the open release PR for dev's `__version__` has a red REQUIRED_CHECKS run on its snapshot head and a bug fix merged to dev after that snapshot; dev is ahead of main by >= 1 merged PR; `ci` `test` on the dev tip is green; no open bump PR; no `hotfix.yml` run in progress and no open hotfix back-merge PR (until it merges, dev's `__version__` is behind the hotfix tag); and it is R-hard's first wake after 11:00 or after 17:00 Asia/Taipei with no bump since then, or a P0/P1 bug fix merged since the last release.
- A bump whose diff is only the `__version__` line (`X.Y.Z -> X.Y.(Z+1)`), optionally plus one new filled `## vX.Y.(Z+1)` section in `docs/whats_new.md` (no other line of that file changed), needs no Opus PASS: mergers merge it once `test` is green. They check it with `python scripts/release_notes.py bump-check --pr N` (exit 0 = no Opus needed) and merge with the printed `at <sha>` as the expected head. Any other file in a bump PR needs an Opus PASS from another Opus run, never the one that opened it.
- The bump role makes only patch bumps. Minor and major bumps are owner-only: the owner says so in an issue comment, then R-hard opens the PR.
- Any other PR that touches `__version__` is a hard stop: add `needs-owner`, do not merge. The one exception is the hotfix back-merge PR.

### Release snapshot
- No freeze on dev: mergers keep merging ready PRs into `dev` while a release is under way.
- Snapshot: when `ci` passes on a dev push and dev's `__version__` X.Y.Z has no tag `vX.Y.Z`, is newer than every tag and its bump commit is not on `main`, `release-pr.yml` creates `release/vX.Y.Z` at the bump commit, dispatches `e2e`, `audio-routing`, `hw-tests` and `install-clean` on it (per-ref concurrency, so dev pushes never cancel them; `ci` and `windows-build` come from that commit's dev push) and opens the release PR. Later dev pushes never move the snapshot.
- Gates on the snapshot head: release PR green (above, newest run per workflow), the hw-tests deferral, the kill switch and a filled `docs/whats_new.md` section. `auto-release.yml` merges it and `release.yml` `plan` re-checks the same gates on the merged snapshot head. Gates are never lowered.
- A red check on the snapshot: re-run it (`gh run rerun <id> --failed`) for a flake, or dispatch the workflow on the snapshot ref (`gh workflow run e2e.yml --ref release/vX.Y.Z`). A real bug: fix it on `dev`, then R-hard opens the next patch bump; `release-pr.yml` then closes the outdated snapshot PR and cuts `release/vX.Y.(Z+1)`. The snapshot never gets new commits.
- If `release-pr.yml` cannot create the branch (the job fails and prints the command), R-hard creates it with the printed `git push origin <sha>:refs/heads/release/vX.Y.Z` and runs `gh workflow run release-pr.yml --ref dev -f dry_run=false`. Nobody else creates, pushes to or deletes `release/v*` branches; `release-pr.yml` deletes them once the tag exists or the version is outdated, and `stale-branches` never touches them.
- When dev's `__version__` changes while a release PR is open (bump reverted or a newer bump), `release-pr.yml` closes that PR with a comment and deletes its branch; until then `auto-release.yml` refuses a snapshot whose version is not dev's current `__version__`. Revert: on the owner's issue comment, R-hard opens a `release: revert __version__ to X.Y.Z` PR (green CI + an Opus PASS).
- The release merges into `main` with a merge commit whose second parent is the snapshot (a dev commit), so dev needs no merge back; section 11 fast-lane commits still do.
- If a failed `release.yml` / `auto-release.yml` run already created the tag, re-run `release.yml` (not `auto-release.yml`, which rejects a version whose tag exists). If the failure is in the code at that commit, R-hard opens a patch bump PR whose body says `release-fix`, with the corrective change in that PR or already on `dev`.
- A release PR open for more than 24 h -> S-hard opens a P0 `[release] release stuck` issue.

### Hotfix lane
- `hotfix.yml` (workflow_dispatch) ships one urgent fix as a patch release outside the cadence: it branches `hotfix/vX.Y.(Z+1)` from the latest release tag, cherry-picks the fix, sets `__version__`, builds and tests once, publishes those tested artifacts, tags the release and opens a back-merge PR `hotfix/vX.Y.Z -> dev`. It is the only release path besides the release PR; it does not change the bump cadence.
- Only R-hard or the integrator dispatches it, only for an open or just-closed P0/P1 `bug` issue whose fix is already merged on `dev` (the input names that commit or PR). Never for features, `maint`, `infra` or unmerged code.
- Never while a `release.yml`, `auto-release.yml` or another `hotfix.yml` run is in progress (one hotfix at a time; `hotfix.yml` has a concurrency group), while a bump PR is open, or while dev's `__version__` has no tag yet; `scripts/hotfix_plan.py` refuses the same cases. The kill switch applies.
- Back-merge: the `hotfix/vX.Y.Z -> dev` PR merges on green CI as a merge commit (not squash or rebase), so the release tag stays reachable from `dev`; mergers add no `needs-opus` for its head name. Its files are the cherry-picked fix (already on `dev`) and `__version__`; any other file is a hard stop (`needs-opus`). The `__version__` change needs no Opus PASS; a cherry-picked fix touching a protected or g4 path does.

### Kill switch
- Auto-release (and the hotfix lane) runs only when the repo variable `AUTO_RELEASE` is `true` AND throttle #574 is at level 1 or above.
