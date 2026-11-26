# Releasing: Step 5: dry runs, rollback, owner actions, pre-releases, release policy

Part of [RELEASING](../RELEASING.md).

5. Dry runs publish nothing: `gh workflow run release.yml --ref <branch>` (build, every check, notes in the job summary) and `gh workflow run release-pr.yml --ref <branch> -f dry_run=true` (the PR title and body in the summary).

   **Rollback** of a bad release `vBAD`: `gh release edit vBAD --prerelease` (the one exception to the #478 rule below: `releases/latest` falls back to the previous stable release), revert the bad change on `dev`, and let the bump role ship a patch bump; that release becomes `latest`. Never delete a published tag or rewrite its assets.

   **Owner one-time actions** (agents do not do these): (1) done: `main` carries `auto-release.yml`, `release.yml` and `release-pr.yml` (`workflow_run` and `schedule` use the copies on `main`; each release carries the newer copies there); (2) turn on `enforce_admins` for `main`; (3) set the repo variable `AUTO_RELEASE=true` (Settings > Secrets and variables > Actions > Variables); (4) Settings > Actions > General > "Allow GitHub Actions to create and approve pull requests"; (5) later, a tag ruleset so only Actions can create `v*` tags. To stop releases at once: set `AUTO_RELEASE` to anything but `true`, or the throttle #574 to `level: 0`.

   Without "Allow GitHub Actions to create and approve pull requests" `release-pr.yml` only writes the PR into its summary and fails. A PR created with the workflow token starts no `pull_request` runs, but its head is the dev commit whose push runs already reported `test`, so the required check on `main` is satisfied. `release-pr.yml` runs from the default branch's copy (`workflow_run`), so it starts working once it is on `main`.

   The README install table links `releases/latest/download/{tinglongwu-setup.exe,mac_install.sh,install.sh}`, so it installs from the newest release that is **not** marked pre-release. The raw `main` scripts fall back to `main` when the ref placeholder is not embedded; do not link them for end users.

   Pre-release from `dev` (#112): set `__version__` to `X.Y.Z-suffix` (for example `1.2.3-beta.1`) on `dev`, wait for `ci`, `e2e`, `windows-build` and `hw-tests` to pass on that commit, then push the tag `vX.Y.Z-suffix` at it. `release.yml` runs on `v*-*` tags: `plan` checks the tag is on `main` or `dev` (a plain `vX.Y.Z` must be on `main` and only comes from the release PR; a hand-pushed plain tag starts nothing), that it equals `v<__version__>` and that the four checks are green on the tagged commit, then the same `windows`, `release` and `windows-installer` jobs publish it marked pre-release, so it never becomes `latest`. `tests/test_release_branch_check.py` runs the branch check against sample tags.

   Release policy (owner decision in #478, amends #345): a plain `vX.Y.Z` tag is a normal release even while the version is 0.x, so the newest 0.x release stays `latest` and the README install links keep working. Do not add `--prerelease` for 0.x tags and do not re-flag published 0.x releases as pre-release: `releases/latest/download/*` skips pre-releases, so every install link would 404. Only a tag with a suffix (`-beta.1`, `-rc.1`) is a pre-release. `tests/test_release_prerelease_rule.py` runs the workflow's rule against sample tags.
