# Benchmarks

Translation quality and speed, measured with `scripts/bench/` (dev tool, not part of the product).

- Hardware: RTX 3050 8 GB. Data: FLORES-200 devtest (1012 sentences, Russian -> Traditional Chinese, gold source text; the 50-sentence rows use the first 50 sentences). Metrics: chrF (sacrebleu) and COMET (Unbabel/wmt22-comet-da). Latency excludes model load. "Previous engine" rows are historical: the Qwen3-4B / Qwen2.5-7B / Qwen3-8B LLMs (Q4_K_M GGUF, 2048 context) that tlwu used before Hy-MT2; they were removed (#276, #283, #308) and the bench can no longer run them (m2m is gone from the bench too).

| engine | chrF | COMET | median s/sentence | peak VRAM (+MiB) |
|---|---|---|---|---|
| opus (removed in #471, historical; was default) | 23.6 | 0.824 | 0.14 | 424 |
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

### Language-pair matrix: en / ru / zh-Hans / zh-Hant / ja / ko (#1305)
Every direction between the six languages (30 cells, rows = source, columns = target), Hy-MT2-1.8B Q4_K_M on the CPU, greedy, first 200 FLORES-200 devtest sentences per direction (the full 1012 stay for the X -> zh-Hant rows above), same chrF + COMET. Each cell is `COMET / chrF`; "(weak)" marks a cell whose COMET is below the weakest shipped direction (en / ru / ja / ko -> zh-Hant) of the same run. zh-Hans <-> zh-Hant is OpenCC script conversion (`tinglongwu.translate.zh_convert`), not MT, as in the app. Directions the app cannot select today are measured too: the table is the data for deciding what to enable. Run it with `gh workflow run eval-flores.yml --ref dev -f matrix=6x6`; the `table` job writes the table to its step summary and the `flores-6x6-table` artifact.

Results: [eval-flores 43893644257](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43893644257) (`matrix=6x6`), commit 1bc72da, first 200 sentences per direction, 4 vCPU GitHub runner. Weakest shipped direction in this run: ru -> zh-Hant, COMET 0.870, so "(weak)" = COMET below 0.870 (COMET is stored to 3 decimals; 0.870 itself is not weak).

| src \ tgt | en | ru | zh-Hans | zh-Hant | ja | ko |
|---|---|---|---|---|---|---|
| en | - | 0.889 / 55.1 | 0.892 / 38.3 | 0.893 / 30.6 | 0.919 / 41.0 | 0.904 / 36.9 |
| ru | 0.854 / 58.2 (weak) | - | 0.865 / 29.9 (weak) | 0.870 / 25.1 | 0.882 / 33.0 | 0.863 / 29.6 (weak) |
| zh-Hans | 0.874 / 57.9 | 0.885 / 47.2 | - | 0.920 / 33.0 (OpenCC) | 0.906 / 34.6 | 0.883 / 31.0 |
| zh-Hant | 0.866 / 56.0 (weak) | 0.870 / 45.3 | 0.907 / 31.7 (OpenCC) | - | 0.902 / 33.0 | 0.879 / 29.1 |
| ja | 0.868 / 54.5 (weak) | 0.871 / 44.6 | 0.884 / 28.0 | 0.889 / 24.3 | - | 0.887 / 32.8 |
| ko | 0.868 / 54.7 (weak) | 0.870 / 45.5 | 0.874 / 28.4 | 0.882 / 24.3 | 0.898 / 36.9 | - |

- Into English is the weakest target on COMET (0.854-0.874) although its chrF is the highest (54.5-58.2): chrF is not comparable across target languages (character n-grams in English vs Chinese / Japanese), compare cells of one column only.
- Russian as the source is the weakest row (4 of 5 cells at or below the shipped floor); every direction into Russian stays at or above it (0.870-0.889).
- Median s/sentence (CPU): -> zh 1.3-2.4, -> en 2.3-2.8, -> ja / ko 2.1-3.9, -> ru 4.1-4.4.

### Hy-MT2 sampling temperature (#107)

Same 1012 FLORES-200 devtest sentences and metrics, Hy-MT2-1.8B Q4_K_M on 4-vCPU GitHub runners, temperature 0.7 (model-card default, shipped before #107) vs 0.2 vs 0 (greedy); top_p 0.6, top_k 20, repeat_penalty 1.05 unchanged. U+FFFD = hypotheses containing the replacement character (suspected cause of #632). Run: [eval-flores 43554407306](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43554407306), commit 3e145b4; per-sentence JSON in its `flores-<src>-t<temp>` artifacts. Each job got a different runner CPU (last column), so s/sentence compares CPUs as much as temperatures.

| direction | temperature | chrF | COMET | U+FFFD lines | median s/sentence | p90 s | CPU |
|---|---|---|---|---|---|---|---|
| rus->zho_Hant | 0.7 | 24.97 | 0.8651 | 0 | 2.38 | 3.64 | Xeon 6975P-C |
| rus->zho_Hant | 0.2 | 25.05 | 0.8657 | 0 | 2.20 | 3.29 | EPYC 7763 |
| rus->zho_Hant | 0 | 25.02 | 0.8653 | 0 | 1.83 | 2.74 | EPYC 9V74 |
| eng->zho_Hant | 0.7 | 30.30 | 0.8934 | 0 | 2.30 | 3.44 | Xeon Platinum 8573C |
| eng->zho_Hant | 0.2 | 30.34 | 0.8934 | 0 | 1.92 | 2.89 | Xeon 6975P-C |
| eng->zho_Hant | 0 | 30.30 | 0.8938 | 0 | 1.67 | 2.44 | EPYC 7763 |
| jpn->zho_Hant | 0.7 | 23.57 | 0.8847 | 0 | 1.28 | 1.93 | EPYC 9V45 |
| jpn->zho_Hant | 0.2 | 23.66 | 0.8850 | 0 | 1.58 | 2.35 | EPYC 9V74 |
| jpn->zho_Hant | 0 | 23.74 | 0.8851 | 0 | 2.15 | 3.25 | Xeon 6975P-C |

All three temperatures are within 0.0006 COMET and 0.2 chrF of each other in every direction (largest gap: jpn chrF 23.57 vs 23.74), and none produced a U+FFFD line, so sampling temperature is not the cause of #632. The greedy rows match the earlier greedy run 43496772451 (above) at its reported precision (chrF 25.0 / 30.3 / 23.7, COMET 0.865 / 0.894 / 0.885). Repeatability was not measured per sentence here: one run per setting.

**Recommendation (shipped since #107): temperature 0 (greedy).** Same quality as 0.7 within the measured range, and greedy decoding has no sampling randomness, so the same prompt gives the same translation (with a glossary, the prompt also depends on the preceding lines, so identical output needs the same glossary context). `TLWU_HYMT_TEMP=0.7` restores the model-card value.

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

## CPU mode A/B: defaults vs CPU mode (#781, #745)

Machines with no NVIDIA GPU (AMD / Intel) run speech recognition and translation on the CPU. CPU mode (#780) picks the speech settings from the PC's cores and RAM. This table compares it with the old defaults on the #569 smoke set (5 clips per focus pair, 20 lines), both runs `--device cpu`, Whisper small, Hy-MT2-1.8B Q4 (CPU, greedy):

- A = defaults: Whisper small, beam 5, default threads.
- B = `tlwu_eval --cpu-mode`: the #780 policy for this runner (4 cores, 15 GB: small, beam 1, 3 ASR threads). Fragment merging is not part of this measurement (the smoke scores whole clips).

Hardware: GitHub-hosted `ubuntu-24.04`, INTEL(R) XEON(R) PLATINUM 8573C, 4 vCPU, 15 GB RAM. Run: [bench-cpu-ab 43809398113](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43809398113), PR #1190. p50 = median end-to-end seconds per line (ASR + MT, `--latency-jsonl`); WER % is CER for ja / zh.

| pair | p50 s (defaults) | p50 s (CPU mode) | chrF (defaults) | chrF (CPU mode) | WER % (defaults) | WER % (CPU mode) |
|---|---|---|---|---|---|---|
| en->zh | 4.635 | 3.861 | 38.90 | 33.98 | 7.77 | 7.77 |
| ja->zh | 6.740 | 5.464 | 19.58 | 19.41 | 15.60 | 16.00 |
| ru->zh | 4.961 | 4.277 | 29.58 | 30.61 | 4.17 | 6.94 |
| zh->en | 5.376 | 4.151 | 48.63 | 42.67 | 13.48 | 14.18 |
| **all** | 5.279 | 4.214 | 34.17 | 31.67 | 10.26 | 11.22 |

Against the #745 targets: p50 drop 20.2% (target >= 30%: missed); chrF drop 2.50 (target <= 1.0: missed); WER rise 0.97 points (target <= 2: met).

An earlier run of the same commit on another runner ([43808855213](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43808855213), CPU not logged) gave the same chrF and WER to the last digit (greedy decoding repeats) and p50 3.680 s -> 3.239 s (12.0% drop). So the quality numbers are stable, and the latency drop is 12-20% depending on the runner CPU.

Why the target is missed: CPU mode only changes speech recognition (ASR time per pair falls 27-38% in the run above: 12.6-17.9 s -> 9.1-11.1 s). Translation takes 2.1-3.1 s per line (median) in both A and B and is the larger part of each line, so it caps the end-to-end gain. The chrF drop comes from beam 1 transcripts (en->zh and zh->en lose 5-6 chrF with the same or nearly the same WER), on only 5 clips per pair. Reproduce: dispatch `.github/workflows/bench-cpu-ab.yml`, or locally `python scripts/bench/cpu_mode_ab.py --run --data eval_data/smoke --base-args "--model small --engine hymt2-cpu" --cpu-args "--engine hymt2-cpu --cpu-mode"`.

### Settings sweep (#745)

Can any Whisper setting reach the #745 targets (p50 -30%, chrF drop <= 1.0, WER rise <= 2) on a 4 vCPU runner? Each row is one more `tlwu_eval --smoke --device cpu` run against A (small, beam 5) on the same runner, Hy-MT2-1.8B unchanged.

Run [43822051019](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43822051019) (Intel Xeon 6975P-C, 4 vCPU, 15 GB). A p50 = 3.655 s:

| variant | p50 s | p50 drop | chrF drop | WER rise (pts) |
|---|---|---|---|---|
| A again (small, beam 5, 4 threads) | 3.692 | -1.0% | 0.00 | 0.00 |
| CPU mode (small, beam 1, 3 threads) | 3.197 | 12.5% | 2.50 | 0.97 |
| small, beam 1, 4 threads | 3.176 | 13.1% | 2.50 | 0.97 |
| base, beam 1, 4 threads | 2.298 | 37.1% | 4.69 | 12.08 |
| base, beam 5, 4 threads | 2.458 | 32.7% | 5.62 | 14.40 |

Run [43819684119](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43819684119) (AMD EPYC 7763, 4 vCPU). A p50 = 4.663 s. The no-timestamps and MT-thread rows used a temporary patch on the branch, not shipped:

| variant | p50 s | p50 drop | chrF drop | WER rise (pts) |
|---|---|---|---|---|
| CPU mode (small, beam 1, 3 threads) | 4.706 | -0.9% | -0.03 | 0.12 |
| small, beam 1, 4 threads | 4.186 | 10.2% | -0.03 | 0.12 |
| small, beam 2, 4 threads, no timestamps | 4.385 | 6.0% | -0.07 | 5.82 |
| small, beam 5, 4 threads, no timestamps | 4.575 | 1.9% | 0.20 | 0.45 |
| small, beam 1, 4 threads, no timestamps, MT 4/4 threads | 4.074 | 12.6% | -0.45 | 5.78 |
| small, beam 1, 4 threads, no timestamps, MT 2/2 threads | 4.122 | 11.6% | -0.45 | 5.78 |

What this shows:
- No Whisper setting meets all three targets. `small` tops out at a 10-13% p50 drop. `base` reaches 33-37%, but WER rises 12-14 points (zh CER 13.5% -> 37.6%, inflated the same way: base writes Traditional characters, see the next bullets; ja 15.6% -> 27.6%) and chrF drops 4.7-5.6.
- Translation is about half of each line (Hy-MT2 median 1.5-2.5 s per line in every row) and no ASR setting changes it. MT 4/4 or 2/2 llama.cpp threads instead of the default 2/4 changed it by less than the run-to-run noise. So the remaining gain has to come from the MT step (#1198).
- With `without_timestamps`, zh CER goes from 14.2% to 36.9%. The recognition is nearly the same: Whisper switches to Traditional characters, the reference is Simplified, and `tlwu_eval` CER does not normalize the script. So this is a scoring gap, not an ASR failure. The setting saved too little time to be worth it either way (11.3% vs 10.2% p50 drop).
- The chrF drop of beam 1 comes from 2 of the 20 clips. On Intel, en_003 loses two commas in the transcript ("To the north, and within easy reach, is" -> "To the north and within easy reach is"). WER ignores punctuation, so it stays 7.77%, but Hy-MT2 then rewords the line and en->zh chrF falls 38.90 -> 33.98. On AMD the same settings give a chrF drop of -0.03. With 5 clips per pair, one changed clip moves a pair's chrF by about 5 and the overall mean by about 1.2, so a 1.0 chrF limit is within the noise of this set. `cpu_mode_ab.py` now lists the clips whose text changed under each table.
- A on the same runner twice: p50 3.655 vs 3.692 s (1% noise), chrF and WER identical. The same commit on different runner CPUs gives different A numbers (p50 3.66-5.28 s; zh->en chrF 43.42 on AMD vs 48.63 on Intel). Compare A and B only within one run.

Reproduce a sweep: put a `scripts/bench/cpu_ab_variants.json` such as `[{"name": "small-b1-t4", "args": "--engine hymt2-cpu --model small --beam 1 --asr-threads 4"}]` on a branch and dispatch `bench-cpu-ab` on it. `tlwu_eval --beam N --asr-threads N` override the defaults and `--cpu-mode`. The workflow installs only Whisper small. A variant with another `--model` needs that model in `$TLWU_MODELS` first (`tlwu-setup --whisper base --models-dir "$TLWU_MODELS"`): run it locally, or add it to the branch's bench run. The base rows above came from a temporary branch patch that ran this setup step.

### MT step on the CPU: quantization and threads (#745, #1198)

Can a smaller Hy-MT2 quant or another thread split give the MT step the speed the p50 -30% target needs? `scripts/bench/mt_cpu_threads.py` in `bench-cpu-ab` run [43861606238](https://github.com/sunososobro-hub/tinglongwu/actions/runs/43861606238) (AMD EPYC 9V74, 4 vCPU = 2 physical cores, 15 GB, commit e3ffbfc), 20 smoke lines, greedy, with Whisper small beam 1 transcribing in the background unless noted. The non-Q4_K_M quants were made on the runner from the published Q8_0 with llama.cpp's quantizer (bench only, never shipped).

Thread plans, Q4_K_M (the shipped quant):

| plan | asr / mt / mt batch threads | MT median s/line | MT p90 s | chrF | Whisper s/clip (during MT) |
|---|---|---|---|---|---|
| old | 3 / 2 / 4 | 3.21 | 4.53 | 48.54 | 3.73 |
| cpu-mode (shipped, #1198) | 2 / 2 / 2 | 2.61 | 3.71 | 48.54 | 2.66 |
| physical | 1 / 1 / 1 | 2.44 | 3.43 | 48.54 | 3.30 |
| mt-heavy | 1 / 3 / 3 | 1.88 | 2.80 | 48.54 | 4.31 |
| old, Whisper off | 3 / 2 / 4 | 1.35 | 1.97 | 48.54 | - |
| cpu-mode, Whisper off | 2 / 2 / 2 | 1.37 | 1.97 | 48.54 | - |

Quants, cpu-mode threads:

| quant | MT median s/line | MT p90 s | chrF | Whisper s/clip (during MT) |
|---|---|---|---|---|
| Q4_K_M (shipped) | 2.64 | 3.64 | 48.54 | 2.71 |
| Q4_0 | 2.93 | 3.94 | 48.20 | 2.74 |
| Q4_K_S | 2.39 | 4.01 | 47.15 | 2.78 |
| IQ4_XS | 3.27 | 4.93 | 48.94 | 2.69 |
| Q3_K_M | 3.25 | 4.80 | 48.27 | 2.41 |
| Q3_K_S | 3.28 | 5.12 | 48.32 | 2.61 |
| IQ3_M | 5.16 | 7.67 | 48.47 | 2.70 |
| Q2_K | 2.94 | 4.25 | 39.73 | 2.60 |

What this shows:
- No quant is faster than Q4_K_M by more than the noise without losing chrF. Q4_K_S is 9% faster at the median but slower at p90 and 1.4 chrF lower; Q2_K loses 8.8 chrF. The 3-bit and IQ quants are slower on this CPU (more dequantization work per token on 2 physical cores).
- Alone, the MT step is 1.35 s per line whatever the thread split. Under load the split only moves time between Whisper and MT: mt-heavy makes MT 28% faster than cpu-mode but Whisper 62% slower, so the line is not faster.
- Together with the settings sweep above: neither Whisper settings, MT threads nor MT quants reach a 30% lower p50 at the #745 quality limits on a 4 vCPU CPU. The shipped CPU mode keeps Q4_K_M and the #1198 thread split. A larger cut needs a different, smaller MT model, which needs its own chrF / COMET eval first.
