# tinglongWu

<p align="center"><img src="assets/mascot/tinglong@2x.png" alt="TingLong, the listening dragon" width="240"><br><strong>聽龍 / TingLong，專心聽的龍。</strong><br>會議與通話用的離線即時語音翻譯。</p>

[English](README.md) | 繁體中文 | [简体中文](README.zh-CN.md) · 🗺️ [路線圖](https://sunososobro-hub.github.io/tinglongwu/roadmap.html) · 📍 [下一版進度](https://github.com/sunososobro-hub/tinglongwu/milestones)

<!-- TODO: screenshot or GIF of the subtitle window (docs/img/demo.gif); no screenshot exists in the repo yet. -->

## 安裝

| 系統 | 指令 |
|---|---|
| Windows 10/11（64 位元） | 下載 [tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe)，執行後勾選同意授權，再一直按 **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux（x86_64，已在 Ubuntu 24.04 測試） | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

<!-- SUPPORT:START -->
| 系統 | 安裝 | 翻譯字幕 | 說話（語音） | GPU | 純 CPU |
|---|:-:|:-:|:-:|:-:|:-:|
| Windows 10/11 | 🟡 | 🟢 | ⚪ | 🟢 | 🟡 |
| macOS | 🟡 | ⚪ | ⚪ | ⚪ | ⚪ |
| Linux（Ubuntu 24.04） | 🟢 | 🟢 | 🟢 | ⚪ | 🟡 |

| 語言 | 聽得懂 | 譯成繁體中文 | 譯成簡體中文 | 譯成英文 | 念出來（語音） |
|---|:-:|:-:|:-:|:-:|:-:|
| 中文 (zh) | 🟢 | — | — | 🟢 | 🟢 Windows |
| English (en) | 🟢 | 🟢 | 🟡 | — | ⚪ |
| 日本語 (ja) | 🟢 | 🟢 | 🟡 | ⚪ | ⚪ |
| Русский (ru) | 🟡 | 🟡 | 🟡 | ⚪ | 🟢 Linux |
| 한국어 (ko) | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |

🟢 已在實機或發版關卡測試 · 🟡 只在 CI 或評測測過，尚未上實機（Windows 安裝：指安裝精靈；可攜版 zip 已實機測試。純 CPU：尚未在實體筆電量延遲） · ⚪ 尚未測試或不支援 · — 同一語言

<p><b>感謝 4 位朋友一起打造</b></p><table><tr><td align="center"><a href="https://github.com/sunososobro-hub"><img src="https://github.com/sunososobro-hub.png?size=60" width="60" height="60" alt="sunososobro-hub"><br><sub><b>sunososobro-hub</b></sub></a><br>💻 📖 🚧</td><td align="center"><a href="https://github.com/cook1e0728"><img src="https://github.com/cook1e0728.png?size=60" width="60" height="60" alt="cook1e0728"><br><sub><b>cook1e0728</b></sub></a><br>💻</td><td align="center"><a href="https://github.com/psgo555"><img src="https://github.com/psgo555.png?size=60" width="60" height="60" alt="psgo555"><br><sub><b>psgo555</b></sub></a><br>🐛</td><td align="center"><a href="https://github.com/jchodev"><img src="https://github.com/jchodev.png?size=60" width="60" height="60" alt="jchodev"><br><sub><b>jchodev</b></sub></a><br>💻 🤔</td></tr></table><p><sub>AI 幫手：🤖 <a href="https://www.anthropic.com/claude">Claude</a>（Anthropic） · 🤖 <a href="https://www.coderabbit.ai">CodeRabbit</a>（程式碼審查）</sub><br><sub>💻 程式 · 🐛 回報問題 · 📖 文件 · 🤔 點子 · 🚧 維護 · 🤖 AI 工具（不算在人數內）</sub></p><!-- SUPPORT:END -->

第一次安裝？先看[安裝時可能看到的畫面](docs/install.md#what-you-might-see-during-install)（未簽章警告是正常的，英文）。

![延遲](docs/img/latency.svg)

## [文件](https://sunososobro-hub.github.io/tinglongwu/docs.html)（安裝選項、系統需求、隱私、FAQ、全部指南） · [完整舊版說明](docs/readme-full.zh-TW.md)

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
