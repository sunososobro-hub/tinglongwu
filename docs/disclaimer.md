# Disclaimer

[Back to README](../README.md)

- **As is.** This software is provided "AS IS" under the MIT license, without warranty of any kind and without liability of the author for any damages (see [LICENSE](../LICENSE)).
- **Research / educational demo.** It is a technology demonstration, not a certified or commercial product.
- **Output can be wrong.** Transcription and translation may contain errors. Do not rely on it for legal, medical, safety or financial decisions.
- **Your responsibility.** You must follow the laws that apply to you and get consent before recording or transcribing other people; see [Acceptable Use / Consent](../README.md#acceptable-use--consent).
- **Third-party components.** Models, libraries and drivers are under their own licenses. You install them from the upstream sources; this repository does not redistribute them (the only third-party binaries committed are the self-built llama.cpp Android AAR under `mobile/`, MIT, and the Windows exe, which bundles the runtime libraries listed in its `licenses/THIRD_PARTY_NOTICES.txt`). See [Licenses and sources](licenses.md).
- **No affiliation.** Not affiliated with or endorsed by Google, Microsoft, Apple, Zoom, Meet, Alibaba (Qwen), Tencent (Hunyuan / Hy-MT2), Meta, OpenAI, NVIDIA, VB-Audio, Existential Audio or any other third party named here. All trademarks belong to their owners.
- **Network guard.** The network guard is best-effort and is not a guarantee that no data leaves your machine.
- **Security.** See [SECURITY.md](../SECURITY.md).
- **Privacy.** See [PRIVACY.md](../PRIVACY.md) ([繁體中文](../PRIVACY.zh-TW.md)).


## Roadmap
Mic -> zh -> ru translation -> built-in-voice offline TTS (Vosk-TTS / Kokoro) -> virtual mic (VB-Cable / BlackHole / PipeWire null-sink), with echo exclusion so synthesized speech is not re-captured.

## Tests
`pip install -r requirements-dev.txt && pytest` (no models or network needed).

## Acceptable Use / Consent (full text)
- Get the consent of every participant before recording, capturing or transcribing a call or meeting; recording and transcription laws vary by jurisdiction.
- Do not use this tool to circumvent DRM or other technical protection of content you have no right to copy.
- Do not use it to impersonate another person. Reverse mode speaks with fixed built-in voices only; there is no voice cloning, and you must not present synthesized speech as someone else's voice.
