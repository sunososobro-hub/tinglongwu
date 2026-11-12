# Releasing

Branches: `main` is stable (users and auto-update follow it); `dev` is where daily work happens. `main` is protected: changes arrive by pull request with the `test` CI check green.

## One click (#414)

1. When `ci` passes on a push to `dev`, `release-pr.yml` opens (or updates) the single `dev` -> `main` PR titled `release: vX.Y.Z...`. The body holds generated release notes: the PRs merged since the last tag, and a thanks list of every non-owner author of a PR or issue (`scripts/release_notes.py`).
   - Next version: `vX.Y.Z-beta.N` -> `-beta.N+1`, `vX.Y.Z` -> `vX.Y.Z+1`. Edit the PR title to pick another (e.g. `release: v0.3.0`); later updates keep it.
   - If you tested the GPU build of that commit on your PC, add the label `gpu-validated-locally`. Otherwise the notes say GPU was not validated.
2. **Merge the PR.** That is the only owner action. `release.yml` then:
   - checks that the push is a merged `dev` -> `main` `release:` PR (any other push to `main` publishes nothing);
   - builds `tinglongwu-win64.zip` on `windows-latest` with `packaging/build_windows.py --allow-cpu-only` (the CUDA package: CUDA DLLs + CUDA llama.cpp + CPU fallback; the flag lets the smoke use the CPU llama fallback because the runner has no NVIDIA driver);
   - runs `packaging/release_check.py` on the zip: size < 2 GiB (GitHub's per-asset limit), `scripts/check_dist_dlls.py` (every DLL dependent resolves, CUDA included), `tlwu.exe --smoke`, and `tlwu.exe --selftest` (CPU, Whisper tiny + OPUS en-zh);
   - creates the tag on the merge commit and publishes the GitHub Release marked **latest**: the zip, `install.sh`, `mac_install.sh`, `install.ps1` (tag embedded in place of `__TLWU_REF__`) and `SHA256SUMS.txt`. The notes include the check table, the zip SHA256 and the GPU statement.

One-time repo setting needed for step 1: Settings > Actions > General > Workflow permissions > "Allow GitHub Actions to create and approve pull requests".

Dry run on any branch (builds and checks everything, publishes nothing): `gh workflow run release.yml --ref <branch>` and `gh workflow run release-pr.yml --ref <branch> -f dry_run=true`.

## By hand

Pushing a tag `vX.Y.Z` that points at a commit on `main` still works: the same build, checks and Release run for that tag.

If the workflow fails, fix and re-run it. If a bad tag was created, delete the Release and the tag (`git push origin :refs/tags/vX.Y.Z`) first.

The README one-command installs use `raw.githubusercontent.com/.../main/scripts/...`; they always run the newest `main` scripts, which fall back to `main` when the ref placeholder is not embedded.
