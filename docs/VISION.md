# Vision

> **Your own computer translates. Your voice never leaves it.**

```mermaid
flowchart LR
    WHY["😣 Language is a wall<br/>in meetings, videos and calls<br/><br/>Today's tools need the cloud,<br/>a subscription and a connection"]
    GOAL["🐉 TingLong<br/>understand · speak · talk both ways<br/>on your own device"]
    WHY ==> GOAL

    GOAL --> MODE["🎧 Mode<br/>listen · speak · two-way"]
    GOAL --> PLAT["💻 Platform<br/>Windows · macOS · Linux · phone"]
    GOAL --> LANG["🌏 Language<br/>tier 1: zh · en · ja · ko<br/>tier 2: vi · th · id · es · de · fr<br/>others: best effort"]

    classDef why fill:#f3f4f6,stroke:#9ca3af,color:#111;
    classDef goal fill:#1e3a5f,stroke:#1e3a5f,color:#fff;
    classDef dim fill:#fff4ea,stroke:#e2731f,color:#111;
    class WHY why; class GOAL goal; class MODE,PLAT,LANG dim;
```

The share of work each mode, platform and language gets follows market trends. The current focus lives on the throttle issue (#574), so this page does not change when the focus does.

## ✅ Principles

| | |
|---|---|
| 🔒 | **Fully offline.** Audio and text stay on the device. |
| 🗑️ | **No recordings kept.** Voiceprints only with each person's consent. |
| 🙂 | **Easy for users, smooth for contributors.** What we ship is plain to anyone; how we work is clear to developers. |
| 🟢 | **Green means tested.** A light turns green only after a real run. |
| 🆓 | **The core stays free and open source.** Paid add-ons, if any, live elsewhere. |

## ⛔ We don't

AI review checks every PR against this list and blocks a PR that crosses it.

| | |
|---|---|
| ☁️ | Send audio or text to a cloud service |
| 🧬 | Remember a voiceprint without consent |
| 📡 | Send usage data home |

Anything that stays inside these lines is welcome. See [CONTRIBUTING.md](../CONTRIBUTING.md) and the [roadmap](https://sunososobro-hub.github.io/tinglongwu/roadmap.html).
