# Benchmarks

Translation quality and speed, measured with `scripts/bench/` (dev tool, not part of the product).

- Hardware: RTX 3050 8 GB. Data: FLORES-200 devtest (1012 sentences, Russian -> Traditional Chinese, gold source text; the 50-sentence rows use the first 50 sentences). Metrics: chrF (sacrebleu) and COMET (Unbabel/wmt22-comet-da). Latency excludes model load. "Previous engine" rows are historical: the Qwen3-4B / Qwen2.5-7B / Qwen3-8B LLMs (Q4_K_M GGUF, 2048 context) that tlwu used before Hy-MT2; they were removed (#276, #283, #308) and the bench can no longer run them (m2m is gone from the bench too).

| engine | chrF | COMET | median s/sentence | peak VRAM (+MiB) |
|---|---|---|---|---|
| opus (default, CPU/GPU) | 23.6 | 0.824 | 0.14 | 424 |
| m2m | 22.9 | 0.819 | 0.34 | 1724 |
| previous engine: 4B LLM (removed) | 23.5 | 0.849 | 0.46 | 3062 |
| previous engine: 7B LLM (removed) | 23.5 | 0.840 | 0.72 | 4682 |
| madlad3b | 26.1 | 0.846 | 0.69 | 3552 |
| previous engine: 8B LLM (removed) | 26.0 | 0.865 | 0.77 | 5148 |
| previous engine: 8B LLM + context (removed) | 26.3 | 0.867 | 0.80 | 5150 |
| Google Cloud Translation v2 (reference) | 29.8 | 0.879 | - | - |

Hy-MT2-1.8B (the engine tlwu ships, Q4_K_M GGUF, llama.cpp on the CPU, greedy `TLWU_HYMT_TEMP=0`) on the same full FLORES-200 devtest, 1012 sentences per direction, same chrF + COMET (#606). Hardware: GitHub-hosted `ubuntu-24.04` runner (4 vCPU, AMD EPYC 9V74 / 9V45), so the s/sentence column is CPU time, not the RTX 3050 above. Run: [eval-flores 43496772451](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43496772451), commit 67958c7; per-sentence JSON in its `flores-<src>` artifacts.

| direction (1012 sentences) | engine | chrF | COMET | median s/sentence (CPU) | p90 s |
|---|---|---|---|---|---|
| rus->zho_Hant | Hy-MT2-1.8B (CPU) | 25.0 | 0.865 | 1.79 | 2.70 |
| rus->zho_Hant | previous engine: 8B LLM (removed, GPU, table above) | 26.0 | 0.865 | 0.77 | - |
| rus->zho_Hant | Google (table above) | 29.8 | 0.879 | - | - |
| eng->zho_Hant | Hy-MT2-1.8B (CPU) | 30.3 | 0.894 | 1.06 | 1.58 |
| jpn->zho_Hant | Hy-MT2-1.8B (CPU) | 23.7 | 0.885 | 1.62 | 2.40 |
| kor->zho_Hant | Hy-MT2-1.8B (CPU, EPYC 9V74, [run 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705)) | 24.2 | 0.879 | 1.63 | 2.42 |
| vie->zho_Hant | Hy-MT2-1.8B (CPU, EPYC 9V74, [run 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705)) | 25.5 | 0.880 | 1.69 | 2.52 |
| tha->zho_Hant | Hy-MT2-1.8B (CPU, EPYC 7763, [run 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705)) | 23.3 | 0.878 | 2.42 | 3.70 |
| ind->zho_Hant | Hy-MT2-1.8B (CPU, Xeon 6975P-C, [run 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705)) | 26.2 | 0.875 | 2.03 | 3.10 |
| spa->zho_Hant | Hy-MT2-1.8B (CPU, EPYC 9V74, [run 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705)) | 24.6 | 0.877 | 1.63 | 2.42 |
| deu->zho_Hant | Hy-MT2-1.8B (CPU, EPYC 9V74, [run 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705)) | 26.5 | 0.877 | 1.60 | 2.38 |
| fra->zho_Hant | Hy-MT2-1.8B (CPU, EPYC 9V45, [run 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705)) | 26.4 | 0.879 | 1.23 | 1.81 |

The Korean and tier-2 rows (#656) come from [eval-flores 43535177705](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43535177705), commit 22efa0f, same settings; each job's runner CPU is in the engine column (4 vCPU each, so s/sentence differs by CPU, e.g. tha on the older EPYC 7763). That run also re-ran rus/eng/jpn and got the same chrF and COMET as above to the last digit (greedy decoding is repeatable). All seven land at COMET 0.875-0.880, between Russian (0.865) and Japanese (0.885).

On Russian, Hy-MT2-1.8B matches the removed 8B LLM on COMET (0.865 vs 0.865, chrF 1.0 lower) and Google stays ahead (0.879). Reproduce on any CPU (Linux paths; the workflow `.github/workflows/eval-flores.yml` runs exactly this, COMET in its own venv because unbabel-comet pins numpy<2):

```
python scripts/bench/fetch_data.py --out ~/bench --fleurs ""
TLWU_HYMT_TEMP=0 python scripts/bench/flores_cpu.py --data ~/bench/flores200_dataset --models-dir ~/tlwu-models --src rus_Cyrl --out out --no-comet
~/comet/bin/python scripts/bench/flores_cpu.py --data ~/bench/flores200_dataset --src rus_Cyrl --out out --score-only
```

| direction (first 50 sentences) | engine | chrF | COMET | median s/sentence |
|---|---|---|---|---|
| en->zho_Hant | opus | 29.9 | 0.854 | 0.10 |
| en->zho_Hant | previous engine: 4B LLM (removed) | 30.7 | 0.881 | 0.54 |
| en->zho_Hant | previous engine: 8B LLM (removed) | 31.2 | 0.892 | 0.91 |
| en->zho_Hant | Google | 37.2 | 0.900 | - |
| ru->eng | opus | 62.2 | 0.867 | 0.07 |
| ru->eng | previous engine: 4B LLM (removed) | 62.9 | 0.867 | 0.52 |
| ru->eng | previous engine: 8B LLM (removed) | 64.3 | 0.870 | 0.81 |
| ru->eng | Google | 67.2 | 0.883 | - |

A 200-utterance FLEURS speech run (ASR large-v3-turbo -> MT) ranks the engines the same way (opus 22.7, previous 8B LLM 24.9, previous 4B LLM 22.3, Google 28.5 chrF).

To reproduce (needs the bench venv, the data under `%USERPROFILE%/tlwu-eval`, and for the Google column a Cloud Translation key in `google_key.txt`; use `--no-google` to skip it):

```
.venv-bench\Scripts\python scripts/bench/run.py --full --engines madlad3b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair en-zh
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair ru-en
```

**Bench only; sends test sentences to that service.** `scripts/bench/run.py` and `google_ref.py` call Google Cloud Translation v2 (and, for the FLEURS run, Speech-to-Text v1, which receives the test audio) only when a key file exists and `--no-google` is not given. They are dev tools: the `tinglongwu` package never imports `scripts/`, and no normal `tlwu` / `tlwu-gui` / reverse-mode path calls them. `fetch_data.py` also downloads the test sets (FLORES-200, FLEURS) from the network.

**Google scores higher than every offline engine** in every direction we tested. The offline engines trade some quality for working with no network and no data leaving the machine. Small samples (50 sentences) are indicative only.

## Japanese eval set (ja -> zh-TW, #346)

50-60 FLEURS ja_jp test utterances (the builder keeps up to `--n 60` and fails below `--min 50`) (read FLORES sentences) with the human FLORES-200 zho_Hant translation of the same sentence as the zh-TW reference. Licences: FLEURS CC-BY 4.0 (audio, ja transcript), FLORES-200 CC-BY-SA 4.0 (reference); the built folder carries a `LICENSE.txt`. The data is downloaded once and never committed.

Windows CMD (in PowerShell use `"$env:USERPROFILE\tlwu-eval\..."`; on Linux/macOS `~/tlwu-eval/...`):

```
python scripts/bench/fetch_data.py --out "%USERPROFILE%\tlwu-eval\bench" --fleurs ja_jp
python scripts/bench/make_ja_set.py --data "%USERPROFILE%\tlwu-eval\bench" --out "%USERPROFILE%\tlwu-eval\ja"
python -m tlwu_eval --smoke --data "%USERPROFILE%\tlwu-eval\ja" --per-lang 60 --out ja.json
```

FLEURS is read news/encyclopedia text, so it covers the broadcast-news style; anime-style dialogue is not in it. The chrF baseline and per-OS numbers are recorded on #346 once measured on GPU hardware.

## Indian speakers: English or Hindi? (#620)

Same 50 FLoRes sentences read in FLEURS en_us (US English) and hi_in (Hindi), Whisper large-v3-turbo + Hy-MT2-1.8B Q4_K_M, both on a 4-vCPU GitHub runner, greedy MT. Run: [eval-india 43521109509](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43521109509) (per-clip JSON in its `india-report` artifact). Rebuild: `.github/workflows/eval-india.yml` / `scripts/bench/india_eval.py`.

| source | ASR WER | chrF vs human zh | chrF from the gold transcript | MT median s |
|---|---|---|---|---|
| English (US accent) -> zh | 6.6% | 34.3 | 38.8 | 1.27 |
| Hindi -> zh, direct | 39.2% | 16.9 | 27.7 | 2.56 |
| Hindi -> en -> zh, pivot | 39.2% | 17.8 | 27.7 | 5.56 |

English gives about twice the Chinese quality of Hindi. Both halves are weaker for Hindi: speech recognition makes 6x the errors, and even from a perfect Hindi transcript Hy-MT2 scores 27.7 vs 38.8. Pivoting through English is no better than the direct route and twice as slow. Not measured yet: Indian-accented English (Svarah is a gated dataset; the runner needs an accepted licence + HF token) and Hinglish.
