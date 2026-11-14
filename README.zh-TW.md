[English](README.md) | 繁體中文 | [简体中文](README.zh-CN.md)

# tinglongWu 聽龍

<p align="center"><img src="assets/demo/listen_en_to_zh.gif" alt="英文語音進、中文字幕出，全程離線" width="560"></p>

```mermaid
flowchart LR
  A["⬇ 下載"] --> B["▶ 播放影片"] --> C["💬 字幕"]
```

| 🎧 聽 | 🗣️ 說 | 🔁 雙向 |
|:-:|:-:|:-:|
| 即時字幕 | 語音輸出 | 雙方互譯 |

<p align="center"><img src="https://img.shields.io/badge/lang-zh%20%C2%B7%20en%20%C2%B7%20ja%20%C2%B7%20ko%20%C2%B7%20ru-blue" alt="zh en ja ko ru"> <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Windows macOS Linux"><br><b><a href="https://github.com/sunososobro-hub/tinglongwu/releases/latest">⬇ 下載 Windows 版</a></b></p>

<details>
<summary>更多：安裝、隱私、常見問題、參與</summary>

離線即時語音翻譯。不上雲、預設不留錄音。

[![Latest release](https://img.shields.io/github/v/release/sunososobro-hub/tinglongwu)](https://github.com/sunososobro-hub/tinglongwu/releases/latest) [![Downloads](https://img.shields.io/github/downloads/sunososobro-hub/tinglongwu/total)](https://github.com/sunososobro-hub/tinglongwu/releases) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) ![Windows | macOS | Linux](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

## 下載

| | 安裝 |
|---|---|
| **Windows 10/11** | **[⬇ 下載 tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe)**，執行後一直按 **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (x86_64) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

Windows 會顯示 SmartScreen 警告（安裝程式尚未簽章，#601 完成前如此）：Edge 會先說「不常下載」且沒有「保留」按鈕：滑到檔案上 → … → 保留 → 點「刪除」旁的 ▾ → 仍要保留；之後在 SmartScreen 畫面點「其他資訊 → 仍要執行」。想先檢查？到[發行頁](https://github.com/sunososobro-hub/tinglongwu/releases/latest)下載 `mac_install.sh` / `install.sh`，讀過並與該頁的 `SHA256SUMS.txt` 比對後再執行。

安裝時會下載一次語音辨識和翻譯模型（需要網路），之後完全離線。

## 語言與電腦需求

目前支援中、英、日、韓、俄，更多語言陸續加入（[語言計畫](docs/VISION.md)）。
一般 CPU 就能跑；有 NVIDIA 顯示卡會快很多。[完整文件：需求、隱私、常見問題](https://sunososobro-hub.github.io/tinglongwu/docs.html) · [平台支援](docs/support.md)

## 想幫忙？

- 📘 [怎麼參與](CONTRIBUTING.md)：歡迎發 PR，預設答案是 yes
- 🟢 [新手友善的題目](https://github.com/sunososobro-hub/tinglongwu/labels/good%20first%20issue)
- 💬 [討論區](https://github.com/sunososobro-hub/tinglongwu/discussions)：問問題、分享你怎麼用、提點子（不用會寫程式）
- 🧭 [願景：我們做什麼、絕不做什麼](docs/VISION.md)
- 🗺️ [路線圖](https://sunososobro-hub.github.io/tinglongwu/roadmap.html) · 📍 [下一版進度](https://github.com/sunososobro-hub/tinglongwu/milestones) · 📊 [專案活躍度](https://sunososobro-hub.github.io/tinglongwu/)

<!-- CONTRIBUTORS:START --><p><b>感謝 4 位朋友一起打造</b></p><table><tr><td align="center"><a href="https://github.com/sunososobro-hub"><img src="https://github.com/sunososobro-hub.png?size=60" width="60" height="60" alt="sunososobro-hub"><br><sub><b>sunososobro-hub</b></sub></a><br>💻 📖 🚧</td><td align="center"><a href="https://github.com/cook1e0728"><img src="https://github.com/cook1e0728.png?size=60" width="60" height="60" alt="cook1e0728"><br><sub><b>cook1e0728</b></sub></a><br>💻</td><td align="center"><a href="https://github.com/psgo555"><img src="https://github.com/psgo555.png?size=60" width="60" height="60" alt="psgo555"><br><sub><b>psgo555</b></sub></a><br>🐛</td><td align="center"><a href="https://github.com/jchodev"><img src="https://github.com/jchodev.png?size=60" width="60" height="60" alt="jchodev"><br><sub><b>jchodev</b></sub></a><br>💻 🤔</td></tr></table><p><sub>AI 幫手：🤖 <a href="https://www.anthropic.com/claude">Claude</a>（Anthropic） · 🤖 <a href="https://www.coderabbit.ai">CodeRabbit</a>（程式碼審查）</sub><br><sub>💻 程式 · 🐛 回報問題 · 📖 文件 · 🤔 點子 · 🚧 維護 · 🤖 AI 工具（不算在人數內）</sub></p><!-- CONTRIBUTORS:END -->

MIT 授權，見 [LICENSE](LICENSE)。(c) 2026 Alonso.

<p align="center"><img src="assets/mascot/tinglong@2x.png" alt="TingLong, the listening dragon" width="120"><br><sub>聽龍 / TingLong，專心聽的龍</sub></p>

</details>
