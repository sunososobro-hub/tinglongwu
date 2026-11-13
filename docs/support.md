# Support and release policy

## Release policy

tinglongWu has **no stable release yet**. Until the stable bar below is met:

- every GitHub release is a **pre-release** and versioned **0.x** (alpha/beta);
- no release is marked "Latest";
- a version `v1.0.0` or higher is only tagged once the stable bar is met.

The README one-command installs use the `main` branch scripts, so they do not depend on a "Latest" release.

### Stable bar (1.0, first "Latest" release)

- One-click install passes a scripted fresh-machine smoke on Windows 11 and Ubuntu 24.04.
- [#294](https://github.com/sunososobro-hub/tinglongwu/issues/294) (one-click GUI launch), [#328](https://github.com/sunososobro-hub/tinglongwu/issues/328) (Linux python3-tk) and [#332](https://github.com/sunososobro-hub/tinglongwu/issues/332) (CPU startup feedback) are closed.
- [#341](https://github.com/sunososobro-hub/tinglongwu/issues/341) (packaged-app smoke in CI) is green and required on `main`.

Related: [#112](https://github.com/sunososobro-hub/tinglongwu/issues/112) (release workflow and pre-release tags), [#71](https://github.com/sunososobro-hub/tinglongwu/issues/71) (fresh-machine install CI), [#340](https://github.com/sunososobro-hub/tinglongwu/issues/340) (quality gate). How to cut a release: [RELEASING](RELEASING.md).

## Platform support

Smoke status = the `install-test` CI workflow: the one-command installer runs from the commit on a clean cloud runner, then `import tinglongwu` and `tlwu --help` must succeed (models are not downloaded). Real-device results: [details](details.md#verification-status).

| Platform | Install method | Smoke status | Known issues |
|---|---|---|---|
| Windows 11 | `bootstrap_windows.ps1` one-liner, `install.bat` (ZIP), unsigned exe zip | CI smoke passes (`windows-latest`); packaged exe not smoke-tested in CI ([#341](https://github.com/sunososobro-hub/tinglongwu/issues/341)) | One-click GUI launch for first-time users ([#294](https://github.com/sunososobro-hub/tinglongwu/issues/294)); speak mode via VB-CABLE not yet verified ([#6](https://github.com/sunososobro-hub/tinglongwu/issues/6)); exe is unsigned ([install](install.md#releases-read-before-trusting-a-binary)) |
| Ubuntu 24.04 | `install.sh` one-liner | CI smoke passes (`ubuntu-24.04`) | Speak mode: first Ctrl+C may hang ([#427](https://github.com/sunososobro-hub/tinglongwu/issues/427)); meeting mode labels both sides as 我 ([#373](https://github.com/sunososobro-hub/tinglongwu/issues/373)); no uninstall script ([#476](https://github.com/sunososobro-hub/tinglongwu/issues/476)) |
| macOS (Apple Silicon) | `mac_install.sh` one-liner (needs BlackHole 2ch) | CI smoke passes (`macos-latest`, BlackHole and models skipped) | Real-device check replaced by CI (`e2e` job on `macos-latest` arm64: listen, speak, meeting on audio files; [#8](https://github.com/sunososobro-hub/tinglongwu/issues/8)); Intel Macs not supported; no packaged app |
