# Third-party components (optional speaker diarization, #219/#220)

| Component | License | Use |
|---|---|---|
| sherpa-onnx / sherpa-onnx-core 2.0.1 (k2-fsa) | Apache-2.0 | runtime for the speaker-embedding model; pinned in `requirements-speaker.txt` |
| 3D-Speaker CAM++ `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` (Alibaba / ModelScope) | Apache-2.0 (3D-Speaker project license) | downloaded on demand by `--speaker-model`, SHA256-pinned, never bundled |

Banned: any `cnceleb`-series speaker model (CN-Celeb data license is non-commercial); the downloader refuses them.
Fallback (not default): `3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx`, same project and license.
