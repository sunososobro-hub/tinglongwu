# tinglongWu details

Moved from the README (#347). The README shows charts only; this page holds the text. It is also published at https://sunososobro-hub.github.io/tinglongwu/docs.html.

tinglongWu (Taiwanese 聽攏有, thiann lóng ū: "I understood it all") is real-time speech translation, offline at runtime and verifiable. Install and model download need the network; the in-process guard is defence in depth, not proof (see [Privacy](privacy.md), "Privacy model").

Example output:

```
[RU] Привет, как дела?
[EN] Hello, how are you?
[ZH] 你好，你過得怎麼樣？
```

Overview: [overview](overview.md). Telling meeting participants first: [consent](acceptable-use.md). Architecture: [ARCHITECTURE](ARCHITECTURE.md) · [zh-TW](ARCHITECTURE.zh-TW.md) · [zh-CN](ARCHITECTURE.zh-CN.md).

Full Chinese translations of the previous long README: [繁體中文](readme-full.zh-TW.md) · [简体中文](readme-full.zh-CN.md).

## Install

| OS | Command |
|---|---|
| Windows (PowerShell) | `irm https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/bootstrap_windows.ps1 \| iex` |
| macOS (Terminal) | `curl -fsSL https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/mac_install.sh \| bash` |
| Linux (bash) | `curl -fsSL https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/install.sh \| bash` |

Unattended Linux (apt yes + default models): `... /install.sh | TLWU_YES=1 bash`. More (checksums, ZIP, GPU): [install](install.md).

## System requirements

| Platform | Status |
|---|---|
| Windows/Linux + NVIDIA GPU | primary |
| Windows/Linux CPU only: 2020+ AVX2, 8+ threads, 16GB | supported |
| Apple Silicon (M1+) | supported |
| Intel Mac, pre-2019 CPU, no AVX2 | not supported (`--force`) |

## Usage

`tlwu-menu` (menu) · `tlwu --lang ru` (live) · `tlwu-gui` (subtitle window): [usage](usage.md).

## Languages

| Language | Code | Status | Modes |
|---|---|---|---|
| Japanese | `ja` | focus | Listen, Speak, Meeting |
| English | `en` | focus | Listen, Speak, Meeting |
| Russian | `ru` | focus | Listen, Speak, Meeting |
| Korean, German, French, Spanish, Italian, Portuguese, Vietnamese, Thai, Indonesian | `ko` `de` `fr` `es` `it` `pt` `vi` `th` `id` | experimental | Listen only |

Details: [models](models.md).

## Verification status

Legend: ✅ verified on a real device, 🧪 not yet verified (links go to the test issue).

| | Windows | macOS | Linux |
|---|---|---|---|
| One-line install | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Listen | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Speak (virtual mic) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/6) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Meeting | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/4) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ (own side) |
| Firewall-blocked offline | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) |
| exe | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/9) | — | — |

## Latency

End-to-end numbers and method: [latency](latency.md). Benchmarks: [benchmarks](benchmarks.md).

## Roadmap

[tlwu roadmap](https://sunososobro-hub.github.io/tinglongwu/roadmap.html)

## Docs

[FAQ](faq.md) · [Privacy](privacy.md) · [Models](models.md) · [Install / GPU](install.md) · [Benchmarks](benchmarks.md) · [Latency](latency.md) · [Licenses](licenses.md) · [Model notices](../THIRD_PARTY_NOTICES.md) · [Linux reverse](linux-reverse.md) · [Acceptable use](acceptable-use.md) · [Tests](testing.md) · [Disclaimer](disclaimer.md) · [SECURITY](../SECURITY.md) · [PRIVACY](../PRIVACY.md)

MIT License. See [LICENSE](../LICENSE). (c) 2026 Alonso.
