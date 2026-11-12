# Latency

End-to-end latency measurements for tinglongwu. This page is a work in progress (see #347); full p50/p90 tables per hardware tier will be generated from eval harness output.

## 2026-11-12 (owner PC, RTX 3050 8 GB)

All rows: model large-v3-turbo, date 2026-11-12, hardware owner PC with RTX 3050 8 GB. These are smoke runs with small samples, so treat them as indicative only. Listen latency is end of speech to subtitle shown; speak latency is end of speech to first TTS audio.

| Direction | Language | Preset | Latency | n | Status |
|-----------|----------|--------|---------|---|--------|
| listen | en | meeting | 2.90 s | 3 (smoke) | measured |
| listen | ru | meeting | 2.46 s | 3 (smoke) | measured |
| listen | en | chat | 1.69 s | 3 (smoke) | measured |
| listen | ru | chat | 2.10 s | 3 (smoke) | measured |
| speak | n/a | n/a | 2.34 s | 6 (smoke, 8 segments minus warm-up; 1 silent, see #316) | measured (lat_speak.py) |

Speak breakdown: VAD 0.97 s + STT 0.56 s + MT 0.33 s + first TTS audio 0.41 s. This does not yet meet the 2 s target.

Measured with external scripts (lat_listen.py, lat_speak.py); native timing is tracked in #323.
