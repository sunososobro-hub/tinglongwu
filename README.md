# tinglongWu

Offline real-time speech translation for meetings and calls.

English | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

| OS | Install (latest release) |
|---|---|
| Windows 10/11 (64-bit) | Download [tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe), run it, accept the license, then keep clicking **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (x86_64, Ubuntu 24.04 tested) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

| OS | Install | Translate subtitles | Speak (voice) | GPU | CPU-only |
|---|:-:|:-:|:-:|:-:|:-:|
| Windows 10/11 | 🟡 | 🟢 | ⚪ | 🟢 | 🟡 |
| macOS | 🟡 | ⚪ | ⚪ | ⚪ | ⚪ |
| Linux (Ubuntu 24.04) | 🟢 | 🟢 | 🟢 | ⚪ | 🟡 |

| Language | Hear it | To 繁體中文 | To 简体中文 | To English | Speak it (voice) |
|---|:-:|:-:|:-:|:-:|:-:|
| 中文 (zh) | 🟢 | — | — | 🟢 | 🟢 Windows |
| English (en) | 🟢 | 🟢 | 🟡 | — | ⚪ |
| 日本語 (ja) | 🟢 | 🟢 | 🟡 | ⚪ | ⚪ |
| Русский (ru) | 🟡 | 🟡 | 🟡 | ⚪ | 🟢 Linux |
| 한국어 (ko) | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |

🟢 tested on a real device or in the release gate · 🟡 tested only in CI or the eval, not yet on a real device (Windows install: the setup wizard; the portable zip is real-device tested. CPU-only: latency not measured on a real laptop) · ⚪ not tested yet or not supported · — same language

<p><b>Built with help from 4 people</b></p><table><tr><td align="center"><a href="https://github.com/sunososobro-hub"><img src="https://github.com/sunososobro-hub.png?size=60" width="60" height="60" alt="sunososobro-hub"><br><sub><b>sunososobro-hub</b></sub></a><br>💻 📖 🚧</td><td align="center"><a href="https://github.com/cook1e0728"><img src="https://github.com/cook1e0728.png?size=60" width="60" height="60" alt="cook1e0728"><br><sub><b>cook1e0728</b></sub></a><br>💻</td><td align="center"><a href="https://github.com/psgo555"><img src="https://github.com/psgo555.png?size=60" width="60" height="60" alt="psgo555"><br><sub><b>psgo555</b></sub></a><br>🐛</td><td align="center"><a href="https://github.com/jchodev"><img src="https://github.com/jchodev.png?size=60" width="60" height="60" alt="jchodev"><br><sub><b>jchodev</b></sub></a><br>💻 🤔</td></tr></table><p><sub>AI helpers: 🤖 <a href="https://www.anthropic.com/claude">Claude</a> (Anthropic) · 🤖 <a href="https://www.coderabbit.ai">CodeRabbit</a> (code review)</sub><br><sub>💻 code · 🐛 bug report · 📖 docs · 🤔 ideas · 🚧 maintenance · 🤖 AI tool (not counted as people)</sub></p>

First time? See [what you might see during install](docs/install.md#what-you-might-see-during-install) (unsigned-app warnings are expected). <!-- TODO: screenshot or GIF of the subtitle window (docs/img/demo.gif); no screenshot exists in the repo yet. -->

![Latency](docs/img/latency.svg)

## [Docs](https://sunososobro-hub.github.io/tinglongwu/docs.html) (install options, requirements, privacy, FAQ, all guides) · [docs/details.md](docs/details.md)

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
