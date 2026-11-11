# tinglongWu 聽攏有

Fully local, offline, real-time speech translator: **system audio -> ASR -> translate -> text** (no cloud, no telemetry, nothing written to disk). Install and model download need the network; runtime does not.

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

```
[RU] Привет, как дела?
[ZH] 你好，你過得怎麼樣？
```

> **Before you use it in a meeting:** tell the other participants you are running live translation. See [Acceptable Use / Consent](#acceptable-use--consent).

<!-- TODO: 30-second demo GIF goes here (docs/demo.gif) -->

## Install (one command)

| OS | Command |
|---|---|
| Windows (PowerShell) | `irm https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.ps1 \| iex` |
| macOS (Terminal) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (bash) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

Details, ZIP install, source install, GPU: [docs/install.md](docs/install.md).

## Use

```
tlwu                       # live system audio, auto language, Traditional Chinese
tlwu --lang ru --model small   # fixed source language
tlwu --file talk.mp3       # decode a file in memory
```

All options, GUI, glossary: [docs/usage.md](docs/usage.md).

## Verification status

✅ verified on a real device, 🧪 not yet verified (links go to the test issue).

| | Windows | macOS | Linux |
|---|---|---|---|
| One-line install | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Listen | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Speak (virtual mic) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/6) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Meeting | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/4) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ (own side) |
| Firewall-blocked offline | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) |
| exe | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/9) | — | — |

Windows modes verified on a real device so far: one-line install and Listen; Speak, Meeting and the firewall test are still pending (see the linked issues).

## More

- [Install](docs/install.md) · [Usage / GUI / glossary](docs/usage.md) · [Models and engines](docs/models.md)
- [Privacy model and firewall test](docs/privacy.md) · [PRIVACY.md](PRIVACY.md) ([繁體中文](PRIVACY.zh-TW.md)) · [SECURITY.md](SECURITY.md)
- [Benchmarks](docs/benchmarks.md) · [Known limitations](docs/troubleshooting.md) · [Linux reverse mode](docs/linux-reverse.md)
- [Architecture](docs/ARCHITECTURE.md) ([zh-TW](docs/ARCHITECTURE.zh-TW.md), [zh-CN](docs/ARCHITECTURE.zh-CN.md)) · [Licenses and sources](docs/licenses.md) · [Disclaimer, roadmap, tests](docs/disclaimer.md)

## Acceptable Use / Consent
- Get the consent of every participant before recording, capturing or transcribing a call or meeting; laws vary by jurisdiction.
- Do not use this tool to circumvent DRM, or to impersonate another person (fixed built-in voices only, no voice cloning).

Provided "AS IS", a research / educational demo; output can be wrong. See [Disclaimer](docs/disclaimer.md). MIT License, see [LICENSE](LICENSE). (c) 2026 Alonso.
