[English](../README.md) | [繁體中文](README.zh-TW.md) | 简体中文

# tinglongWu 听龙

<p align="center"><img src="../assets/demo/listen_en_to_zh.gif" alt="英文语音进、中文字幕出，全程离线" width="560"></p>

<p align="center"><a href="https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe"><img src="../assets/readme/download.zh-CN.svg" alt="⬇ 下载 Windows 版" width="300"></a><br>77 MB + 1.6 GB 模型 · SmartScreen：更多信息 → 仍要运行</p>

```mermaid
flowchart LR
  A["⬇ 下载"] --> B["▶ 播放视频"] --> C["💬 字幕"]
```

<p align="center"><img src="../assets/readme/modes.zh-CN.svg" alt="🎧 听：实时字幕 · 🗣️ 说：语音输出 · 🔁 双向：双方互译" width="720"></p>

<p align="center"><img src="../assets/readme/facts.zh-CN.svg" alt="语言：中、英、日、韩、俄；印地语实验 · Windows、Linux、macOS（未验证）" width="720"></p>

<details>
<summary>更多：安装、隐私、常见问题、参与</summary>

<p align="center"><img src="../assets/readme/privacy.zh-CN.svg" alt="离线实时语音翻译：不上云、默认不留录音、不收集个人数据。更新检查只匿名计数（日期、版本、系统）。" width="720"></p>

[![Latest release](https://img.shields.io/github/v/release/sunososobro-hub/tinglongwu)](https://github.com/sunososobro-hub/tinglongwu/releases/latest) [![Downloads](https://img.shields.io/github/downloads/sunososobro-hub/tinglongwu/total)](https://github.com/sunososobro-hub/tinglongwu/releases) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](../LICENSE)

## 下载

<p align="center"><img src="../assets/readme/steps.zh-CN.svg" alt="下载、运行一直点 Next、模型下载一次、之后离线出字幕。普通 CPU 就能跑，有 NVIDIA 显卡快很多。" width="720"></p>

| | 安装 |
|---|---|
| **Windows 10/11** | **[⬇ 下载 tinglongwu-setup.exe](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-setup.exe)**，运行后一直点 **Next** |
| macOS | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (x86_64) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

Windows 会显示 SmartScreen 警告（安装程序尚未签名，#601 完成前如此）：Edge 会先说“不常下载”且没有“保留”按钮：鼠标移到下载项 → … → 保留 → 点“删除”旁的 ▾ → 仍要保留；之后在 SmartScreen 画面点“更多信息 → 仍要运行”。想先检查？到[发布页](https://github.com/sunososobro-hub/tinglongwu/releases/latest)下载 `mac_install.sh` / `install.sh`，读过并与该页的 `SHA256SUMS.txt` 比对后再运行。

安装时会下载一次语音识别和翻译模型（需要网络），之后翻译、音频和字幕完全离线，从不联网。自动更新（默认开启）每天检查一次 GitHub，不发送任何 ID；安装时取消勾选或设置 `TLWU_OFFLINE=1` 即完全不联网。

## 语言与电脑需求

更多语言陆续加入（[语言计划](VISION.md)）。[完整文档：需求、隐私、常见问题](https://sunososobro-hub.github.io/tinglongwu/docs.html) · [平台支持](support.md)

## 想帮忙？

- 📘 [怎么参与](../.github/CONTRIBUTING.md)：欢迎提 PR，默认答案是 yes
- 🟢 [新手友好的题目](https://github.com/sunososobro-hub/tinglongwu/labels/good%20first%20issue)
- 🐞 [用你自己的 AI 报告问题](report-with-ai.zh-TW.md)：不用 GitHub 账号，复制一段提示贴给 ChatGPT / Claude / Gemini（繁体页面）
- 💬 [讨论区](https://github.com/sunososobro-hub/tinglongwu/discussions)：提问题、分享你怎么用、提点子（不用会写代码）
- 🧭 [愿景：我们做什么、绝不做什么](VISION.md)
- 🗺️ [路线图](https://sunososobro-hub.github.io/tinglongwu/roadmap.html) · 📍 [下一版进度](https://github.com/sunososobro-hub/tinglongwu/milestones) · 📊 [项目活跃度](https://sunososobro-hub.github.io/tinglongwu/)

<!-- CONTRIBUTORS:START --><p><b>感谢 7 位朋友一起打造</b></p><p><a href="https://github.com/sunososobro-hub" title="💻 📖 🚧"><img src="https://github.com/sunososobro-hub.png?size=48" alt="sunososobro-hub"></a> <a href="https://github.com/cook1e0728" title="💻"><img src="https://github.com/cook1e0728.png?size=48" alt="cook1e0728"></a> <a href="https://github.com/psgo555" title="🐛"><img src="https://github.com/psgo555.png?size=48" alt="psgo555"></a> <a href="https://github.com/jchodev" title="💻 🤔"><img src="https://github.com/jchodev.png?size=48" alt="jchodev"></a> <a href="https://github.com/Jerry-Tseng" title="🐛 🤔"><img src="https://github.com/Jerry-Tseng.png?size=48" alt="Jerry-Tseng"></a> <a href="https://github.com/lewis0731" title="🐛 🤔"><img src="https://github.com/lewis0731.png?size=48" alt="lewis0731"></a> <a href="https://github.com/chengchihchen3-art" title="🐛"><img src="https://github.com/chengchihchen3-art.png?size=48" alt="chengchihchen3-art"></a></p><p><sub><a href="details.md#contributors">图示说明与 AI 帮手</a></sub></p><!-- CONTRIBUTORS:END -->

MIT 授权，见 [LICENSE](../LICENSE)。(c) 2026 Alonso.

<p align="center"><img src="../assets/mascot/tinglong%402x.png" alt="听龙 / TingLong，专心听的龙" width="120"><br><sub>听龙 / TingLong，专心听的龙</sub></p>

</details>
