# Platform support and release policy

## Release policy

tinglongWu has **no stable release yet**. Until the stable bar below is met, releases are versioned **0.x** (alpha/beta quality) and `v1.0.0` or higher is not tagged.

- A plain `v0.X.Y` tag is a normal GitHub release, so the newest one is "Latest" and the README install links (`releases/latest/download/...`) keep working (owner decision in [#478](https://github.com/sunososobro-hub/tinglongwu/issues/478), amends [#345](https://github.com/sunososobro-hub/tinglongwu/issues/345)).
- A tag with a suffix (`-beta.1`, `-rc.1`) is a GitHub pre-release and never becomes "Latest".
- 0.x means "not stable yet": read the known issues below before relying on it.

How to cut a release: [RELEASING](RELEASING.md).

### Stable bar (1.0)

- One-click install passes a scripted fresh-machine smoke on Windows 11 and Ubuntu 24.04.
- [#294](https://github.com/sunososobro-hub/tinglongwu/issues/294) (one-click GUI launch), [#328](https://github.com/sunososobro-hub/tinglongwu/issues/328) (Linux python3-tk) and [#332](https://github.com/sunososobro-hub/tinglongwu/issues/332) (CPU startup feedback) are closed.
- [#341](https://github.com/sunososobro-hub/tinglongwu/issues/341) (packaged-app smoke in CI) is green and required on `main`.

Related: [#112](https://github.com/sunososobro-hub/tinglongwu/issues/112) (release workflow and pre-release tags), [#71](https://github.com/sunososobro-hub/tinglongwu/issues/71) (fresh-machine install CI), [#340](https://github.com/sunososobro-hub/tinglongwu/issues/340) (quality gate).

## Platform support

Smoke status comes from CI on clean cloud runners, with model downloads skipped: the `install-test` workflow runs the one-command installer from the commit, then `tlwu --help` must succeed. Real-device results: [details](details.md#verification-status).

| Platform | Install method | Smoke status | Known issues |
|---|---|---|---|
| Windows 11 | `tinglongwu-setup.exe` wizard (README), `install.bat` (source ZIP), portable `tinglongwu-win64.zip` | CI on `windows-latest` (a Windows Server image): `install-test` (bootstrap script), `install-clean` (`install.bat` and the wizard on a runner with no Python), `windows-build` (`tlwu.exe --smoke`). No Windows 11 fresh-machine smoke yet; GUI launch of the packaged app not smoke-tested in CI ([#341](https://github.com/sunososobro-hub/tinglongwu/issues/341)) | One-click GUI launch for first-time users ([#294](https://github.com/sunososobro-hub/tinglongwu/issues/294)); speak mode via VB-CABLE not yet verified ([#6](https://github.com/sunososobro-hub/tinglongwu/issues/6)); the wizard and the zip are unsigned ([install](install.md#releases-read-before-trusting-a-binary)) |
| Ubuntu 24.04 | `install.sh` one-liner (README) | CI `install-test` passes on `ubuntu-24.04` | Speak mode: first Ctrl+C may hang ([#427](https://github.com/sunososobro-hub/tinglongwu/issues/427)); no uninstall script ([#476](https://github.com/sunososobro-hub/tinglongwu/issues/476)) |
| macOS | `mac_install.sh` one-liner (README; needs BlackHole 2ch for speak mode) | CI `install-test` passes on `macos-latest` (Apple Silicon; BlackHole and models skipped); the `e2e` workflow runs listen, speak and meeting on audio files there | No real-Mac smoke per release; Intel Macs are not tested in CI; no packaged app (out of scope) |
