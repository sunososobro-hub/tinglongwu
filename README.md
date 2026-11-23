English | [繁體中文](docs/README.zh-TW.md) | [简体中文](docs/README.zh-CN.md)

# tinglongWu 聽龍

<p align="center"><img src="assets/demo/listen_en_to_zh.gif" alt="English speech in, Chinese subtitles out, translated on this PC" width="560"></p>

<p align="center"><a href="https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe"><img src="assets/readme/download.en.svg" alt="⬇ Download for Windows" width="300"></a><br>77 MB + 1.6 GB models · SmartScreen: More info → Run anyway</p>

```mermaid
flowchart LR
  A["⬇ Download"] --> B["▶ Play a video"] --> C["💬 Subtitles"]
```

<p align="center"><img src="assets/readme/modes.en.svg" alt="🎧 Listen: live subtitles · 🗣️ Speak: your voice, translated · 🔁 Two-way: both sides" width="720"></p>

<p align="center"><img src="assets/readme/facts.en.svg" alt="Languages: zh, en, ja, ko, ru; hi experimental · Windows, Linux, macOS (unverified)" width="720"></p>

<details>
<summary>More: install, privacy, FAQ, contribute</summary>

<p align="center"><img src="assets/readme/privacy.en.svg" alt="No cloud, no recordings kept by default. No personal data is collected. Update checks are counted anonymously (date, version, OS) to know how many installs are active." width="720"></p>

[![Latest release](https://img.shields.io/github/v/release/sunososobro-hub/tinglongwu)](https://github.com/sunososobro-hub/tinglongwu/releases/latest) [![Downloads](https://img.shields.io/github/downloads/sunososobro-hub/tinglongwu/total)](https://github.com/sunososobro-hub/tinglongwu/releases) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![install-test](https://github.com/sunososobro-hub/tinglongwu/actions/workflows/install-test.yml/badge.svg?branch=dev)](https://github.com/sunososobro-hub/tinglongwu/actions/workflows/install-test.yml)

## Download

<p align="center"><img src="assets/readme/steps.en.svg" alt="Download, Next, models once, offline subtitles; CPU ok, NVIDIA GPU faster" width="720"></p>

| | Install |
|---|---|
| **Windows 10/11** | **[⬇ Download tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe)**, run it, keep clicking **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (x86_64) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

SmartScreen warns because the installer is unsigned until #601 lands; Edge hides Keep: hover the download → **…** → **Keep** → open the ▾ next to **Delete** → **Keep anyway**; then click **More info → Run anyway**. Prefer to inspect first? Download `mac_install.sh` / `install.sh` from the [release](https://github.com/sunososobro-hub/tinglongwu/releases/latest), read it, compare it with `SHA256SUMS.txt` on that page, then run it.

**Update (Windows):** if you kept *Update automatically* ticked in setup, the app checks GitHub once a day, downloads the new version and asks **Restart now?**; any time: ⚙ Adjust → **Check for updates**. Models and settings are kept. Remove on macOS/Linux: `bash ~/tinglongwu/scripts/uninstall.sh` (models stay unless you add `--purge`).

Setup downloads speech and translation models once; optional packs (extra languages, speak mode, GPU) also need internet once. After that translation, audio and subtitles never use the network. Auto-update (default on in the Windows installer; the `git` update in PRIVACY.md is off by default) sends no ID; untick it in setup or set `TLWU_OFFLINE=1` for zero network requests.

## Languages and requirements

[More languages coming](docs/VISION.md). [Full docs (FAQ, privacy)](https://sunososobro-hub.github.io/tinglongwu/docs.html) · [Platforms](docs/support.md) · [Language pairs](docs/languages.md) · [Report with AI](docs/report-with-ai.md) · [Glossary with AI](docs/glossary-with-ai.md)

## Want to help?

- 📘 [How to contribute](.github/CONTRIBUTING.md): PRs are welcome, the default answer is yes
- 🟢 [Good first issues](https://github.com/sunososobro-hub/tinglongwu/labels/good%20first%20issue)
- 💬 [Discussions](https://github.com/sunososobro-hub/tinglongwu/discussions): ask, show your setup, share an idea (no GitHub skills needed)
- 🧭 [Vision: what we build and what we never do](docs/VISION.md)
- 🗺️ [Roadmap](https://sunososobro-hub.github.io/tinglongwu/roadmap.html) · 📍 [Next release progress](https://github.com/sunososobro-hub/tinglongwu/milestones) · 📊 [Project activity](https://sunososobro-hub.github.io/tinglongwu/)

<!-- CONTRIBUTORS:START --><p><b>Built with help from 5 people</b></p><p><a href="https://github.com/sunososobro-hub" title="sunososobro-hub 💻 📖 🚧"><img src="https://github.com/sunososobro-hub.png?size=60" width="48" alt="sunososobro-hub"></a> <a href="https://github.com/cook1e0728" title="cook1e0728 💻"><img src="https://github.com/cook1e0728.png?size=60" width="48" alt="cook1e0728"></a> <a href="https://github.com/psgo555" title="psgo555 🐛"><img src="https://github.com/psgo555.png?size=60" width="48" alt="psgo555"></a> <a href="https://github.com/jchodev" title="jchodev 💻 🤔"><img src="https://github.com/jchodev.png?size=60" width="48" alt="jchodev"></a> <a href="https://github.com/Jerry-Tseng" title="Jerry-Tseng 🐛 🤔"><img src="https://github.com/Jerry-Tseng.png?size=60" width="48" alt="Jerry-Tseng"></a></p><p><sub>AI helpers: 🤖 <a href="https://www.anthropic.com/claude">Claude</a> (Anthropic) · 🤖 <a href="https://www.coderabbit.ai">CodeRabbit</a> (code review)</sub><br><sub>💻 code · 🐛 bug report · 📖 docs · 🤔 ideas · 🚧 maintenance · 🤖 AI tool (not counted as people)</sub></p><!-- CONTRIBUTORS:END -->

MIT License. See [LICENSE](LICENSE). [Code signing policy](docs/CODE_SIGNING_POLICY.md) (SignPath Foundation). (c) 2026 Alonso.

<p align="center"><img src="assets/mascot/tinglong%402x.png" alt="TingLong, the listening dragon" width="120"><br><sub>聽龍 / TingLong, the listening dragon</sub></p>

</details>
