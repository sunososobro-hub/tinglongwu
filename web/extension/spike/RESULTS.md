# Chrome extension spike results (#1158, parent #711)

Run: `extension-spike` workflow run 43784837759 on PR #1167 (head 42972cf), 2026-11-16.
GitHub ubuntu-24.04 runner, 4 vCPU, no GPU; headless Chromium 153, transformers.js 4.3.2, ONNX Runtime Web WASM,
4 threads, cross-origin isolated. 20 CI smoke clips (`eval/ci_smoke.json`: 5 each en / ja / ru / zh, FLEURS).
Latency = ASR + MT inference seconds for one whole clip (4-12 s of speech), timed inside the page; it leaves out
audio capture/chunking, the zh script conversion and drawing the line, so the real end of speech -> subtitle time is
somewhat longer (part 2/3 measure that).
Desktop chrF in brackets = `eval/baseline.json` (Whisper small + Hy-MT2 1.8B Q4, same clips, same runner type).

| ASR | MT | download MB | load cold s | load warm s | latency median s | p90 s | ASR median s | MT median s | chrF en->zh (desktop) | chrF ja->zh (desktop) | chrF ru->zh (desktop) | chrF zh->en (desktop) | ASR WER en | ASR CER ja | ASR WER ru | ASR CER zh |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| whisper-tiny | opus-mt | 515.1 | 12.9 | 2.9 | 2.02 | 3.42 | 1.16 | 0.9 | 13.74 (38.9) | 2.4 (19.58) | 14.26 (29.58) | 24.68 (43.42) | 0.35 | 2.816 | 0.403 | 0.475 |
| whisper-base | opus-mt | 551.1 | 13.5 | 3.1 | 2.86 | 3.66 | 2.07 | 0.74 | 15.9 (38.9) | 8.45 (19.58) | 19.54 (29.58) | 31.0 (43.42) | 0.117 | 0.324 | 0.125 | 0.397 |
| whisper-tiny | nllb-600m | 955.6 | 16.2 | 14.6 | 5.27 | 6.72 | 1.16 | 4.21 | 9.02 (38.9) | 3.24 (19.58) | 13.33 (29.58) | 27.87 (43.42) | 0.35 | 2.816 | 0.403 | 0.475 |
| whisper-base | nllb-600m | 991.6 | 16.8 | 14.8 | 5.94 | 7.94 | 2.07 | 3.91 | 18.9 (38.9) | 10.07 (19.58) | 19.41 (29.58) | 39.32 (43.42) | 0.117 | 0.324 | 0.125 | 0.397 |
| whisper-small | opus-mt | 723.3 | 15.1 | 3.8 | 6.78 | 8.22 | 6.06 | 0.78 | 20.32 (38.9) | 7.85 (19.58) | 22.76 (29.58) | 39.63 (43.42) | 0.087 | 0.176 | 0.069 | 0.191 |
| whisper-small | nllb-600m | 1163.8 | 18.4 | 15.5 | 9.76 | 12.42 | 6.06 | 3.97 | 18.59 (38.9) | 13.24 (19.58) | 17.9 (29.58) | 40.6 (43.42) | 0.087 | 0.176 | 0.069 | 0.191 |

Model sizes (bytes the page fetched, q8): whisper-tiny 43.6 MB, whisper-base 79.7 MB, whisper-small 251.8 MB;
opus-mt en-zh 119.5, ja-en 114.7, ru-en 117.8, zh-en 119.5 MB; NLLB-200 distilled 600M 912.0 MB.
"download MB" above sums every MT model of the candidate (all 4 pairs); one user needs only their pair:
whisper-small + opus-mt for en->zh is 371 MB, for ru->zh (pivot ru-en + en-zh) 489 MB.

## Choice

- ASR: **whisper-small** (`onnx-community/whisper-small`, q8). MT: **opus-mt** (`Xenova/opus-mt-*`, q8; ja/ru -> zh
  pivot through en). Median 6.78 s, p90 8.22 s on this 4-vCPU runner: meets the #711 median <= 8 s budget, p90 does not.
- Quality gap: the browser stack is well below the desktop pipeline (mean chrF 22.6 vs 32.9 desktop on the same
  clips). ASR is the same Whisper small model as desktop (q8 here), so MT (opus-mt vs Hy-MT2 1.8B) is the likely main cause,
  worst on ja->zh (7.85 vs 19.58, a two-hop pivot); this run does not isolate it (no MT-only score on the same
  transcripts, no desktop ASR error rate). NLLB-600M is better for ja->zh and zh->en but 912 MB and ~4 s per line.
- Not usable: whisper-tiny (ja CER 2.8 = repetition loops); whisper-small + NLLB (median 9.76 s, over budget).
- WebGPU: the runner has no GPU, so only WASM (CPU) was measured; it is the floor
  for a laptop without GPU. A WebGPU number needs a real PC (owner test on #711).

## Follow-ups for the next parts
- zh->ru is not measured: the smoke set has zh->en references only. opus-mt has no zh->ru model (pivot zh-en +
  en-ru, or NLLB); needs a zh->ru reference set first.
- Part 2 (#1159): the offscreen document needs `cross_origin_embedder_policy` / `cross_origin_opener_policy` in the
  manifest for multi-threaded WASM (this spike ran cross-origin isolated); ONNX Runtime wasm must be packaged, not
  loaded from a CDN (MV3 forbids remote code).
- Better ja->zh / ru->zh MT in the browser (a direct model, or a small LLM on WebGPU) is the open quality question.

Re-run: the `extension-spike` workflow (PR paths or workflow_dispatch); local: see `scripts/bench/extension_spike.py`.
