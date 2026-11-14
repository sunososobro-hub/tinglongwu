# tinglongWu 聽龍: offline real-time speech translation

**Translate meetings and calls live, on your own computer. No cloud, no recordings kept by default.**

[![Latest release](https://img.shields.io/github/v/release/sunososobro-hub/tinglongwu)](https://github.com/sunososobro-hub/tinglongwu/releases/latest) [![Downloads](https://img.shields.io/github/downloads/sunososobro-hub/tinglongwu/total)](https://github.com/sunososobro-hub/tinglongwu/releases) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![install-test](https://github.com/sunososobro-hub/tinglongwu/actions/workflows/install-test.yml/badge.svg?branch=dev)](https://github.com/sunososobro-hub/tinglongwu/actions/workflows/install-test.yml) ![Windows | macOS | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

English | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

<p align="center"><img src="assets/demo/listen_en_to_zh.gif" alt="English speech in, Chinese subtitles out, fully offline" width="560"><br>*English audio in, Chinese subtitles out, nothing leaves the PC.*</p>

## Download

| | Install |
|---|---|
| **Windows 10/11** | **[⬇ Download tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe)**, run it, keep clicking **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (x86_64) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

Windows shows a SmartScreen warning because the installer is unsigned until #601 lands: Edge first says the file "isn't commonly downloaded" and hides Keep: hover the download → **…** → **Keep** → open the ▾ next to **Delete** → **Keep anyway**; then on the SmartScreen screen click **More info → Run anyway**. Prefer to inspect first? Download `mac_install.sh` / `install.sh` from the [release](https://github.com/sunososobro-hub/tinglongwu/releases/latest), read it, compare it with `SHA256SUMS.txt` on that page, then run it.

To remove it on macOS/Linux: run the same installer with `--uninstall` (`bash ~/tinglongwu/scripts/uninstall.sh`; downloaded models stay unless you add `--purge`).

Setup downloads the speech and translation models once (internet needed). After that it runs fully offline.

## Three ways to use it

| 🎧 Listen | 🗣️ Speak | 🔁 Two-way meeting |
|---|---|---|
| Subtitles for what others say in videos, calls and meetings | Say it in your language; the translation is spoken into the call through a virtual mic | Both sides at once, subtitles marked **me** / **them** |

## Why tinglongWu

- 🆓 **Free and open source** (MIT)
- 🔒 **Offline**: audio and text never leave your computer
- 🗑️ **No recordings kept by default** (opt-in `--record`, #239), no account, no tracking

## Languages and requirements

Chinese, English, Japanese, Korean and Russian today; more on the way ([language plan](docs/VISION.md)).
Runs on a normal CPU; an NVIDIA GPU makes it much faster. [Full docs: requirements, privacy, FAQ](https://sunososobro-hub.github.io/tinglongwu/docs.html) · [Platform support](docs/support.md) · [Language pairs](docs/languages.md)

## Want to help?

- 📘 [How to contribute](CONTRIBUTING.md): PRs are welcome, the default answer is yes
- 🟢 [Good first issues](https://github.com/sunososobro-hub/tinglongwu/labels/good%20first%20issue)
- 💬 [Discussions](https://github.com/sunososobro-hub/tinglongwu/discussions): ask a question, show how you use it, share an idea (no GitHub skills needed)
- 🧭 [Vision: what we build and what we never do](docs/VISION.md)
- 🗺️ [Roadmap](https://sunososobro-hub.github.io/tinglongwu/roadmap.html) · 📍 [Next release progress](https://github.com/sunososobro-hub/tinglongwu/milestones) · 📊 [Project activity](https://sunososobro-hub.github.io/tinglongwu/)

<!-- CONTRIBUTORS:START --><p><b>Built with help from 4 people</b></p><table><tr><td align="center"><a href="https://github.com/sunososobro-hub"><img src="https://github.com/sunososobro-hub.png?size=60" width="60" height="60" alt="sunososobro-hub"><br><sub><b>sunososobro-hub</b></sub></a><br>💻 📖 🚧</td><td align="center"><a href="https://github.com/cook1e0728"><img src="https://github.com/cook1e0728.png?size=60" width="60" height="60" alt="cook1e0728"><br><sub><b>cook1e0728</b></sub></a><br>💻</td><td align="center"><a href="https://github.com/psgo555"><img src="https://github.com/psgo555.png?size=60" width="60" height="60" alt="psgo555"><br><sub><b>psgo555</b></sub></a><br>🐛</td><td align="center"><a href="https://github.com/jchodev"><img src="https://github.com/jchodev.png?size=60" width="60" height="60" alt="jchodev"><br><sub><b>jchodev</b></sub></a><br>💻 🤔</td></tr></table><p><sub>AI helpers: 🤖 <a href="https://www.anthropic.com/claude">Claude</a> (Anthropic) · 🤖 <a href="https://www.coderabbit.ai">CodeRabbit</a> (code review)</sub><br><sub>💻 code · 🐛 bug report · 📖 docs · 🤔 ideas · 🚧 maintenance · 🤖 AI tool (not counted as people)</sub></p><!-- CONTRIBUTORS:END -->

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.

<p align="center"><img src="assets/mascot/tinglong@2x.png" alt="TingLong, the listening dragon" width="120"><br><sub>聽龍 / TingLong, the listening dragon</sub></p>
