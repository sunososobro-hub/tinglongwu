[English](../README.md) | 繁體中文 | [简体中文](README.zh-CN.md)

# tinglongWu 聽龍

<p align="center"><img src="../assets/demo/listen_en_to_zh.gif" alt="英文語音進、中文字幕出，全程離線" width="560"></p>

<p align="center"><a href="https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe"><img src="../assets/readme/download.zh-TW.svg" alt="⬇ 下載 Windows 版" width="300"></a><br>77 MB + 1.6 GB 模型 · SmartScreen：其他資訊 → 仍要執行</p>

```mermaid
flowchart LR
  A["⬇ 下載"] --> B["▶ 播放影片"] --> C["💬 字幕"]
```

<p align="center"><img src="../assets/readme/modes.zh-TW.svg" alt="🎧 聽：即時字幕 · 🗣️ 說：語音輸出 · 🔁 雙向：雙方互譯" width="720"></p>

<p align="center"><img src="../assets/readme/facts.zh-TW.svg" alt="語言：中、英、日、韓、俄；印地語實驗 · Windows、Linux、macOS（未驗證）" width="720"></p>

<details>
<summary>更多：安裝、隱私、常見問題、參與</summary>

<p align="center"><img src="../assets/readme/privacy.zh-TW.svg" alt="離線即時語音翻譯：不上雲、預設不留錄音、不收個資。更新檢查只匿名計數（日期、版本、系統）。" width="720"></p>

[![Latest release](https://img.shields.io/github/v/release/sunososobro-hub/tinglongwu)](https://github.com/sunososobro-hub/tinglongwu/releases/latest) [![Downloads](https://img.shields.io/github/downloads/sunososobro-hub/tinglongwu/total)](https://github.com/sunososobro-hub/tinglongwu/releases) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](../LICENSE)

## 下載

<p align="center"><img src="../assets/readme/steps.zh-TW.svg" alt="下載、執行一直按 Next、模型下載一次、之後離線出字幕。一般 CPU 就能跑，有 NVIDIA 顯示卡快很多。" width="720"></p>

| | 安裝 |
|---|---|
| **Windows 10/11** | **[⬇ 下載 tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe)**，執行後一直按 **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (x86_64) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

Windows 會顯示 SmartScreen 警告（安裝程式尚未簽章，#601 完成前如此）：Edge 會先說「不常下載」且沒有「保留」按鈕：滑到檔案上 → … → 保留 → 點「刪除」旁的 ▾ → 仍要保留；之後在 SmartScreen 畫面點「其他資訊 → 仍要執行」。想先檢查？到[發行頁](https://github.com/sunososobro-hub/tinglongwu/releases/latest)下載 `mac_install.sh` / `install.sh`，讀過並與該頁的 `SHA256SUMS.txt` 比對後再執行。

安裝時會下載一次語音辨識和翻譯模型（需要網路），之後翻譯、音訊和字幕完全離線，從不連網。自動更新（預設開啟）每天檢查一次 GitHub，不傳送任何 ID；安裝時取消勾選或設定 `TLWU_OFFLINE=1` 即完全不連網。

## 語言與電腦需求

更多語言陸續加入（[語言計畫](VISION.md)）。[完整文件：需求、隱私、常見問題](https://sunososobro-hub.github.io/tinglongwu/docs.html) · [平台支援](support.md)

## 想幫忙？

- 📘 [怎麼參與](../.github/CONTRIBUTING.md)：歡迎發 PR，預設答案是 yes
- 🟢 [新手友善的題目](https://github.com/sunososobro-hub/tinglongwu/labels/good%20first%20issue)
- 🐞 [用你自己的 AI 回報問題](report-with-ai.zh-TW.md)：不用 GitHub 帳號，複製一段提示貼給 ChatGPT / Claude / Gemini
- 💬 [討論區](https://github.com/sunososobro-hub/tinglongwu/discussions)：問問題、分享你怎麼用、提點子（不用會寫程式）
- 🧭 [願景：我們做什麼、絕不做什麼](VISION.md)
- 🗺️ [路線圖](https://sunososobro-hub.github.io/tinglongwu/roadmap.html) · 📍 [下一版進度](https://github.com/sunososobro-hub/tinglongwu/milestones) · 📊 [專案活躍度](https://sunososobro-hub.github.io/tinglongwu/)

<!-- CONTRIBUTORS:START --><p><b>感謝 7 位朋友一起打造</b></p><p><a href="https://github.com/sunososobro-hub" title="💻 📖 🚧"><img src="https://github.com/sunososobro-hub.png?size=48" alt="sunososobro-hub"></a> <a href="https://github.com/cook1e0728" title="💻"><img src="https://github.com/cook1e0728.png?size=48" alt="cook1e0728"></a> <a href="https://github.com/psgo555" title="🐛"><img src="https://github.com/psgo555.png?size=48" alt="psgo555"></a> <a href="https://github.com/jchodev" title="💻 🤔"><img src="https://github.com/jchodev.png?size=48" alt="jchodev"></a> <a href="https://github.com/Jerry-Tseng" title="🐛 🤔"><img src="https://github.com/Jerry-Tseng.png?size=48" alt="Jerry-Tseng"></a> <a href="https://github.com/lewis0731" title="🐛 🤔"><img src="https://github.com/lewis0731.png?size=48" alt="lewis0731"></a> <a href="https://github.com/chengchihchen3-art" title="🐛"><img src="https://github.com/chengchihchen3-art.png?size=48" alt="chengchihchen3-art"></a></p><p><sub><a href="details.md#contributors">圖示說明與 AI 幫手</a></sub></p><!-- CONTRIBUTORS:END -->

MIT 授權，見 [LICENSE](../LICENSE)。[程式碼簽章政策](CODE_SIGNING_POLICY.md)（SignPath Foundation）。(c) 2026 Alonso.

<p align="center"><img src="../assets/mascot/tinglong%402x.png" alt="聽龍 / TingLong，專心聽的龍" width="120"><br><sub>聽龍 / TingLong，專心聽的龍</sub></p>

</details>
