# tinglongWu — Taiwanese 聽攏有 (thiann lóng ū): "I understood it all". Real-time speech translation, offline at runtime and verifiable (install and model download need the network; the in-process guard is defence in depth, not proof, see Privacy model).
[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md) · Architecture / 架構 / 架构: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [zh-TW](docs/ARCHITECTURE.zh-TW.md) · [zh-CN](docs/ARCHITECTURE.zh-CN.md)

```
[RU] Привет, как дела?
[EN] Hello, how are you?
[ZH] 你好，你過得怎麼樣？
```

Fully local and offline: [overview](docs/overview.md). Telling meeting participants first: [consent](docs/acceptable-use.md).

## One-command install (Windows / Mac / Linux)
| OS | Command |
|---|---|
| Windows (PowerShell) | `irm https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.ps1 \| iex` |
| macOS (Terminal) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (bash) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

More (checksums, ZIP, GPU): [docs/install.md](docs/install.md).

## System requirements
| Platform | Status |
|---|---|
| Windows/Linux + NVIDIA GPU | primary |
| Windows/Linux CPU only: 2020+ AVX2, 8+ threads, 16GB | supported |
| Apple Silicon (M1+) | supported |
| Intel Mac, pre-2019 CPU, no AVX2 | not supported (`--force`) |

## Usage
`tlwu-menu` (menu) · `tlwu --lang ru` (live) · `tlwu-gui` (subtitle window): [docs/usage.md](docs/usage.md).

## Languages
| Language | Code | Status | Modes |
|---|---|---|---|
| Japanese | `ja` | focus | Listen, Speak, Meeting |
| English | `en` | focus | Listen, Speak, Meeting |
| Russian | `ru` | focus | Listen, Speak, Meeting |
| Korean, German, French, Spanish, Italian, Portuguese, Vietnamese, Thai, Indonesian | `ko` `de` `fr` `es` `it` `pt` `vi` `th` `id` | experimental | Listen only |

Details: [docs/models.md](docs/models.md).

## Roadmap
**[tlwu roadmap](https://sunososobro-hub.github.io/tinglongwu/roadmap.html)**

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

## More docs
[Privacy](docs/privacy.md) · [Models](docs/models.md) · [Install / GPU](docs/install.md) · [Benchmarks](docs/benchmarks.md) · [Licenses](docs/licenses.md) · [Model notices](THIRD_PARTY_NOTICES.md) · [Linux reverse](docs/linux-reverse.md) · [Acceptable use](docs/acceptable-use.md) · [Tests](docs/testing.md) · [Disclaimer](docs/disclaimer.md) · [SECURITY](SECURITY.md) · [PRIVACY](PRIVACY.md)

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
