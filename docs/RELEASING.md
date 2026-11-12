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

3. The `release` workflow checks the tag is on `main`, embeds the tag into `install.sh`, `mac_install.sh` and `install.ps1` (placeholder `__TLWU_REF__`), and publishes them with `SHA256SUMS.txt` and auto-generated notes. The README one-command installs use `raw.githubusercontent.com/.../main/scripts/...` (releases are all pre-releases, so `releases/latest/download/...` would 404); they always run the newest `main` scripts, which fall back to `main` when the ref placeholder is not embedded.

If the workflow fails (for example the tag is not on `main`), delete the tag (`git push origin :refs/tags/vX.Y.Z`), fix, and tag again.
