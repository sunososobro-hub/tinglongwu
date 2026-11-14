# Latency

End-to-end latency measurements for tinglongwu (#347). The generated block below holds p50 / p90 per hardware tier (cpu-low, mid-gpu, high), language pair and preset. To add a run: on the machine being measured, play a clip through `tlwu --file CLIP.wav --paced --lang en --model large-v3-turbo --preset meeting --latency-jsonl lat.jsonl` (at least 30 utterances), then `python scripts/make_latency_doc.py pack lat.jsonl --tier mid-gpu --hardware "RTX 3050 8 GB" --model large-v3-turbo --engine hymt --preset meeting --pair 'en->zh-TW'`, commit the file it writes under eval/latency/, and run `python scripts/make_latency_doc.py`. Why a line comes after the speech: see the [FAQ](faq.md).

<!-- LATENCY:START (python scripts/make_latency_doc.py) -->
## Measured runs (generated)

Latency here is end of speech to the translated subtitle shown (#323 `--latency-jsonl`, field `total`), from a real-time paced clip. p50 / p90 are nearest-rank percentiles; cells with fewer than 30 utterances are marked indicative. Do not edit this block by hand: add a run with `python scripts/make_latency_doc.py pack ...` and rerun the script.

No measured runs are committed yet (eval/latency/ is empty), so there is no table and no chart yet.
<!-- LATENCY:END -->

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

## Pause threshold A/B (#21)

Listen mode has two pause knobs, both off their defaults only when set: `--end-silence SEC` (env `TLWU_END_SILENCE`, default 0.6 s: VAD silence that ends a segment) and `--pause-send SEC` (env `TLWU_PAUSE_SEND`, default 0.8 s, ja 0.5 s: a pause this long sends an unfinished sentence at once). To measure the proposed 0.5 s, run the same paced clip twice with `--latency-jsonl`, once with defaults and once with `--end-silence 0.5 --pause-send 0.5`, and compare the median `total` and the number of lines that end mid-sentence. Keep `--pause-send` at or below `--end-silence` + `--vad-pad` (0.1 s): a cut reports at least that much silence but often not more, so a higher value can turn the early send off for most cuts and tlwu warns.
