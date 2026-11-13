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

3. The `release` workflow checks the tag is on `main`, embeds the tag into `install.sh`, `mac_install.sh` and `install.ps1` (placeholder `__TLWU_REF__`), and publishes them with `SHA256SUMS.txt` and auto-generated notes. A second job, `windows-installer`, builds the setup wizard with Inno Setup and attaches it twice, as `tinglongwu-setup-X.Y.Z.exe` and as the fixed name `tinglongwu-setup.exe`, and adds both hashes to `SHA256SUMS.txt`. The README install table links `releases/latest/download/{tinglongwu-setup.exe,mac_install.sh,install.sh}`, so it installs from the newest release that is **not** marked pre-release (a pre-release or draft never becomes `latest`). If you replace the wizard by hand, upload it under both names and update `SHA256SUMS.txt`, or the Windows link breaks. The portable `tinglongwu-win64.zip` is built and uploaded by hand under exactly that name (README links it as the portable option); list its hash in `SHA256SUMS.txt` too. The raw `main` scripts fall back to `main` when the ref placeholder is not embedded; do not link them for end users.

4. Edit the release notes. Users read the top, developers open the details. Use this order:

   1. `## Download`: one link per OS in the order Windows (`tinglongwu-setup-X.Y.Z.exe`), macOS, Linux, each with a one-line hint for the warning the user will see (SmartScreen, Mac password for BlackHole, run as normal user / apt y/N).
   2. The support table, copied from the README top and updated to what was actually tested for this release. Only mark 🟢 what someone ran on a real machine for this release; keep the README table in sync:

      ```
      | OS | Install | Translate subtitles | Speak (voice) | GPU | CPU-only |
      |---|:-:|:-:|:-:|:-:|:-:|
      | Windows 10/11 | ... |
      | macOS | ... |
      | Linux (Ubuntu 24.04) | ... |

      🟢 tested on a real device · 🟡 install tested in CI only · ⚪ not tested yet
      ```

   3. Contributors: `Built with help from N people:` and one avatar per login in `.all-contributorsrc` (all-time, reporters included; the list only grows),
      `<a href="https://github.com/LOGIN"><img src="https://github.com/LOGIN.png?size=40" width="40" height="40" alt="LOGIN"></a>`.
      GitHub's own "Contributors" block at the bottom lists only users @mentioned in that release's notes, so @mention every all-time contributor in the details section (step 5) to fill it with everyone.
   4. `## What's new`: at most 3 plain-language bullets about what the user notices. No PR or issue numbers.
   5. `<details><summary>For developers</summary>`: the full changelog with PR/issue links, every asset with its SHA-256, test and gate notes, known issues, the @mention thanks (say what is in this tag: check with `git merge-base --is-ancestor <merge-commit> vX.Y.Z`), and a "How to contribute" link to `CONTRIBUTING.md` and the `good first issue` label.

   The README top follows the same order with steps 1-3 only.

If the workflow fails (for example the tag is not on `main`), delete the tag (`git push origin :refs/tags/vX.Y.Z`), fix, and tag again.
