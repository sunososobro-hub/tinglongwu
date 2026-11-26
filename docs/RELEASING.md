# Releasing

Branches: `main` is stable (users and auto-update follow it); `dev` is where daily work happens. `main` is protected: changes arrive by pull request with the `test` CI check green.

Releases are automatic (#589, owner decision 2026-11-13; rules: `.agent/rules_release.md` section 14 and `.agent/tlwu_rules.md` section 17). Nobody clicks merge.

Two lanes (#976, owner 2026-11-15): `dev` is the fast lane (a PR merges on `test` alone; `dev-build.yml` publishes every dev push as the rolling `dev-latest` pre-release), `main` is the quality lane. A release is cut from a snapshot branch `release/vX.Y.Z` that points at the dev commit of the version bump (or, when the bump had no `## vX.Y.Z` section in `docs/whats_new.md`, the first later dev commit that adds it; until then nothing is cut and the plan says "waiting for whats_new section", #1012), so later merges to `dev` never restart, cancel or freeze it, and every release gate runs on that snapshot.

## Steps

One file per step, so unrelated release changes stop editing one shared file (#1917). Read them in this order:

- [Step 1: bump the version](releasing/1_bump.md)
- [Step 2: snapshot branch and release PR](releasing/2_snapshot.md)
- [Step 3: auto-merge gate](releasing/3_auto_merge.md)
- [Step 4: publish (release.yml)](releasing/4_publish.md)
- [Step 5: dry runs, rollback, owner actions, pre-releases, release policy](releasing/5_dry_run_rollback.md)
- [Step 6: support tables and contributors](releasing/6_support_tables.md)
- [Step 7: release notes and editing a release](releasing/7_release_notes.md)
- [When a release run fails](releasing/8_failed_release.md)
