# Benchmarks

Translation quality and speed, measured with `scripts/bench/` (dev tool, not part of the product).

- Hardware: RTX 3050 8 GB. Data: FLORES-200 devtest (1012 sentences, Russian -> Traditional Chinese, gold source text; the 50-sentence rows use the first 50 sentences). Metrics: chrF (sacrebleu) and COMET (Unbabel/wmt22-comet-da). Latency excludes model load. Qwen engines are Q4_K_M GGUF, 2048 context.

| engine | chrF | COMET | median s/sentence | peak VRAM (+MiB) |
|---|---|---|---|---|
| opus (default, CPU/GPU) | 23.6 | 0.824 | 0.14 | 424 |
| m2m | 22.9 | 0.819 | 0.34 | 1724 |
| qwen3-4b | 23.5 | 0.849 | 0.46 | 3062 |
| qwen25-7b | 23.5 | 0.840 | 0.72 | 4682 |
| madlad3b | 26.1 | 0.846 | 0.69 | 3552 |
| qwen3-8b | 26.0 | 0.865 | 0.77 | 5148 |
| qwen3-8b + context | 26.3 | 0.867 | 0.80 | 5150 |
| Google Cloud Translation v2 (reference) | 29.8 | 0.879 | - | - |

| direction (first 50 sentences) | engine | chrF | COMET | median s/sentence |
|---|---|---|---|---|
| en->zho_Hant | opus | 29.9 | 0.854 | 0.10 |
| en->zho_Hant | qwen3-4b | 30.7 | 0.881 | 0.54 |
| en->zho_Hant | qwen3-8b | 31.2 | 0.892 | 0.91 |
| en->zho_Hant | Google | 37.2 | 0.900 | - |
| ru->eng | opus | 62.2 | 0.867 | 0.07 |
| ru->eng | qwen3-4b | 62.9 | 0.867 | 0.52 |
| ru->eng | qwen3-8b | 64.3 | 0.870 | 0.81 |
| ru->eng | Google | 67.2 | 0.883 | - |

A 200-utterance FLEURS speech run (ASR large-v3-turbo -> MT) ranks the engines the same way (opus 22.7, qwen3-8b 24.9, qwen3-4b 22.3, Google 28.5 chrF).

To reproduce (needs the bench venv, the data under `%USERPROFILE%/tlwu-eval`, and for the Google column a Cloud Translation key in `google_key.txt`; use `--no-google` to skip it):

```
.venv-bench\Scripts\python scripts/bench/run.py --full --engines m2m,qwen3-8b,qwen3-8b+ctx,madlad3b,qwen3-4b,qwen25-7b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair en-zh --engines qwen3-8b,qwen3-4b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair ru-en --engines qwen3-8b,qwen3-4b
```

**Bench only; sends test sentences to that service.** `scripts/bench/run.py` and `google_ref.py` call Google Cloud Translation v2 (and, for the FLEURS run, Speech-to-Text v1, which receives the test audio) only when a key file exists and `--no-google` is not given. They are dev tools: the `tinglongwu` package never imports `scripts/`, and no normal `tlwu` / `tlwu-gui` / reverse-mode path calls them. `fetch_data.py` also downloads the test sets (FLORES-200, FLEURS) from the network.

**Google scores higher than every offline engine** in every direction we tested. The offline engines trade some quality for working with no network and no data leaving the machine. Small samples (50 sentences) are indicative only.
