# tinglongWu

Offline real-time speech translation for meetings and calls.

English | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

<!-- TODO: screenshot or GIF of the subtitle window (docs/img/demo.gif); no screenshot exists in the repo yet. -->

## Install

| OS | Command |
|---|---|
| Windows (PowerShell) | `irm https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/bootstrap_windows.ps1 \| iex` |
| macOS (Terminal) | `curl -fsSL https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/mac_install.sh \| bash` |
| Linux (bash) | `curl -fsSL https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/install.sh \| bash` |

### What you might see during install

tinglongWu is a small open-source project and does not pay for a Windows code-signing certificate or an Apple Developer account, so a few warnings are normal:

- **Windows:** the installer and release downloads are not code-signed. Microsoft SmartScreen may show "Windows protected your PC": click **More info**, then **Run anyway**. Some antivirus tools may also flag unsigned builds. The source is public, and every release lists SHA256 checksums (`SHA256SUMS.txt`) so you can verify your download.
- **macOS:** the install is the one-line Terminal command above (no app to double-click), so there is no "unidentified developer" prompt for tinglongWu itself. Expect: Homebrew installs Python, git and the BlackHole audio drivers and asks for your Mac password once; if macOS blocks a driver installer, allow it in System Settings > Privacy & Security (**Open Anyway**) and re-run the command; a restart may be needed before BlackHole shows up; on first run macOS asks to let Terminal use the microphone (click OK, or enable it in Privacy & Security > Microphone).
- **Linux:** nothing special. The script asks y/N before any `apt` step.

## Verification status

![Verification status](docs/img/verification.svg)

![Latency](docs/img/latency.svg)

![Languages](docs/img/languages.svg)

## Docs

**[Docs (install options, requirements, privacy, FAQ, all guides)](https://sunososobro-hub.github.io/tinglongwu/docs.html)** · [docs/details.md](docs/details.md)

## Contributors

Contributions of any kind welcome — bug reports count too.

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/sunososobro-hub"><img src="https://avatars.githubusercontent.com/u/243713070?v=4?s=80" width="80px;" alt="sunososobro-hub"/><br /><sub><b>sunososobro-hub</b></sub></a><br /><a href="https://github.com/sunososobro-hub/tinglongwu/commits?author=sunososobro-hub" title="Code">💻</a> <a href="https://github.com/sunososobro-hub/tinglongwu/commits?author=sunososobro-hub" title="Documentation">📖</a> <a href="#maintenance-sunososobro-hub" title="Maintenance">🚧</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/cook1e0728"><img src="https://avatars.githubusercontent.com/u/202496468?v=4?s=80" width="80px;" alt="cook1e0728"/><br /><sub><b>cook1e0728</b></sub></a><br /><a href="https://github.com/sunososobro-hub/tinglongwu/commits?author=cook1e0728" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/psgo555"><img src="https://avatars.githubusercontent.com/u/59055362?v=4?s=80" width="80px;" alt="psgo555"/><br /><sub><b>psgo555</b></sub></a><br /><a href="https://github.com/sunososobro-hub/tinglongwu/issues?q=author%3Apsgo555" title="Bug reports">🐛</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/jchodev"><img src="https://avatars.githubusercontent.com/u/100594737?v=4?s=80" width="80px;" alt="jchodev"/><br /><sub><b>jchodev</b></sub></a><br /><a href="https://github.com/sunososobro-hub/tinglongwu/commits?author=jchodev" title="Code">💻</a> <a href="#ideas-jchodev" title="Ideas, Planning, & Feedback">🤔</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
