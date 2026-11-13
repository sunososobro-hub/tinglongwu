# tinglongWu

會議與通話用的離線即時語音翻譯。

[English](README.md) | 繁體中文 | [简体中文](README.zh-CN.md)

<!-- TODO: screenshot or GIF of the subtitle window (docs/img/demo.gif); no screenshot exists in the repo yet. -->

## 安裝

| 系統 | 指令 |
|---|---|
| Windows 10/11（64 位元） | 下載 [tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe)，執行後勾選同意授權，再一直按 **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux（x86_64，已在 Ubuntu 24.04 測試） | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

| 系統 | 安裝 | 翻譯字幕 | 說話（語音） | GPU | 純 CPU |
|---|:-:|:-:|:-:|:-:|:-:|
| Windows 10/11 | 🟡 | 🟢 | ⚪ | 🟢 | 🟡 |
| macOS | 🟡 | ⚪ | ⚪ | ⚪ | ⚪ |
| Linux（Ubuntu 24.04） | 🟢 | 🟢 | 🟢 | ⚪ | 🟡 |

| 語言 | 聽得懂 | 譯成中文 | 譯成英文 | 念出來（語音） |
|---|:-:|:-:|:-:|:-:|
| 中文 (zh) | 🟢 | — | 🟢 | 🟢 |
| English (en) | 🟢 | 🟢 | — | ⚪ |
| 日本語 (ja) | 🟢 | 🟢 | ⚪ | ⚪ |
| Русский (ru) | 🟡 | 🟡 | ⚪ | 🟡 |
| 한국어 (ko) | ⚪ | ⚪ | ⚪ | ⚪ |

🟢 已在實機或發版關卡測試 · 🟡 只在 CI 或評測測過，尚未上實機（Windows 安裝：指安裝精靈；可攜版 zip 已實機測試。純 CPU：尚未在實體筆電量延遲） · ⚪ 尚未測試或不支援 · — 同一語言

第一次安裝？先看[安裝時可能看到的畫面](docs/install.md#what-you-might-see-during-install)（未簽章警告是正常的，英文）。

感謝 4 位朋友一起打造：<a href="https://github.com/sunososobro-hub"><img src="https://github.com/sunososobro-hub.png?size=40" width="40" height="40" alt="sunososobro-hub"></a> <a href="https://github.com/cook1e0728"><img src="https://github.com/cook1e0728.png?size=40" width="40" height="40" alt="cook1e0728"></a> <a href="https://github.com/psgo555"><img src="https://github.com/psgo555.png?size=40" width="40" height="40" alt="psgo555"></a> <a href="https://github.com/jchodev"><img src="https://github.com/jchodev.png?size=40" width="40" height="40" alt="jchodev"></a>

![延遲](docs/img/latency.svg)

## 文件

**[文件（安裝選項、系統需求、隱私、FAQ、全部指南）](https://sunososobro-hub.github.io/tinglongwu/docs.html)** · [完整舊版說明](docs/readme-full.zh-TW.md)

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
