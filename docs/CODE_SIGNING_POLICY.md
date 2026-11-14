# Code signing policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).

Status: the SignPath Foundation application has been submitted. Windows releases stay unsigned until it is approved; this page describes the process that will apply once signing is enabled.

## What is signed
- `tlwu.exe` and `tinglongwu-setup*.exe` (Windows), built from this repository by GitHub Actions (`.github/workflows/release.yml`).
- Only binaries built from this repository's source are signed. Upstream third-party binaries are not signed with this certificate.

## Roles
- **Authors** (can change the source code): [@sunososobro-hub](https://github.com/sunososobro-hub)
- **Reviewers** (review changes from non-committers, i.e. pull requests from contributors): [@sunososobro-hub](https://github.com/sunososobro-hub)
- **Approvers** (approve every signing request): [@sunososobro-hub](https://github.com/sunososobro-hub)

## Process
1. A release tag triggers the GitHub Actions build.
2. The workflow submits the built artifacts to SignPath.
3. An approver manually approves each signing request in SignPath.
4. The signed binaries are attached to the GitHub release.

## Privacy
See [PRIVACY.md](../PRIVACY.md). The runtime (`tlwu`) makes no network connections; only `tlwu-setup` downloads models, and only when you run it.
