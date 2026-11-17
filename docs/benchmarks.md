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

Results: [eval-flores 44099762645](https://github.com/sunososobro-hub/tinglongwu/actions/runs/44099762645) (`matrix=6x6`, after the #1351 echo fix), commit 8a6c5b2, first 200 sentences per direction, 4 vCPU GitHub runner. Weakest shipped direction in this run: ru -> zh-Hant, COMET 0.870, so "(weak)" = COMET below 0.870 (COMET is stored to 3 decimals; 0.870 itself is not weak).

Traffic lights by COMET (rounded to 3 decimals): 🟢 >= 0.880, 🟡 0.870-0.879, 🔴 < 0.870 (0.870 = weakest shipped direction in this run, ru -> zh-Hant). `G` = Google Cloud Translation v2 COMET with the gap to ours (positive = Google ahead); `G -` = no Google number yet. The Google numbers come from different sentence sets (ru -> zh-Hant 1012 sentences, ru -> en and en -> zh-Hant 50) and are listed in `scripts/bench/data/google_refs.json`; ours are the first 200. The table is generated: `python scripts/bench/matrix_lights.py scripts/bench/data/matrix_6x6_44099762645.json` (the `table` job of `eval-flores.yml` prints it for every `matrix=6x6` run). After every published release, `eval-matrix-release.yml` runs the matrix once on the release tag (never a release gate) and opens a `[eval] refresh the 6x6 matrix ...` issue with the new table, so this section is updated by a normal PR. chrF per cell stays in the run's `flores-6x6-table` artifact.

| source \ target | en | ru | zh-Hans | zh-Hant | ja | ko |
|---|---|---|---|---|---|---|
| **en** |  | 🟢 0.889<br>G - | 🟢 0.892<br>G - | 🟢 0.893<br>G 0.900 (gap +0.007) | 🟢 0.919<br>G - | 🟢 0.904<br>G - |
| **ru** | 🔴 0.861<br>G 0.883 (gap +0.022) |  | 🔴 0.865<br>G - | 🟡 0.870<br>G 0.879 (gap +0.009) | 🟢 0.882<br>G - | 🔴 0.863<br>G - |
| **zh-Hans** | 🟡 0.874<br>G - | 🟢 0.885<br>G - |  | 🟢 0.920 (script conversion)<br>G - | 🟢 0.906<br>G - | 🟢 0.883<br>G - |
| **zh-Hant** | 🔴 0.866<br>G - | 🟡 0.870<br>G - | 🟢 0.907 (script conversion)<br>G - |  | 🟢 0.902<br>G - | 🟡 0.879<br>G - |
| **ja** | 🔴 0.869<br>G - | 🟡 0.871<br>G - | 🟢 0.884<br>G - | 🟢 0.889<br>G - |  | 🟢 0.887<br>G - |
| **ko** | 🟡 0.877<br>G - | 🟡 0.870<br>G - | 🟡 0.874<br>G - | 🟢 0.882<br>G - | 🟢 0.898<br>G - |  |

17 green / 8 yellow / 5 red (green >= 0.880, yellow 0.870-0.879, red < 0.870); largest gap to Google: ru -> en +0.022

Cells whose COMET changed by >= 0.005 vs the earlier run 43893644257 (before the #1351 echo fix): ru -> en 0.854 -> 0.861, ko -> en 0.868 -> 0.877. All other cells moved by <= 0.001. The `echo` category of `flores_cpu.py --breakdown` is 0 sentences (0%) in all 30 cells of this run.

- Into English is the weakest target on COMET (0.861-0.877) although its chrF is the highest (54.5-58.5): chrF is not comparable across target languages (character n-grams in English vs Chinese / Japanese), compare cells of one column only.
- Russian as the source is the weakest row (4 of 5 cells at or below the shipped floor); every direction into Russian stays at or above it (0.870-0.889).
- Median s/sentence (CPU): -> zh 1.3-2.4, -> en 2.3-2.8, -> ja / ko 2.1-3.9, -> ru 4.1-4.4.

### Why Russian as the source is weak (#1351)
Same data as the 6x6 run (first 200 FLORES-200 devtest sentences, Hy-MT2-1.8B, greedy, 4 vCPU GitHub runners); `eval-flores` now writes an error breakdown of the 40 lowest-COMET sentences of every direction (`flores_cpu.py --breakdown`, heuristic categories, one sentence can have several) and takes experiment `variants` and `tgts`. Runs: before = [44013625413](https://github.com/sunososobro-hub/tinglongwu/actions/runs/44013625413) (commit 1d4ad28), after = [44016598460](https://github.com/sunososobro-hub/tinglongwu/actions/runs/44016598460) (commit 712198c). Reproduce on any revision that has the `variants` / `tgts` inputs (#1351 onwards): `gh workflow run eval-flores.yml --ref <ref> -f langs=rus_Cyrl -f tgts=zho_Hant,eng_Latn -f limit=200 -f temps=0 -f variants=card,src,q8`. `--ref` takes a branch or tag; on dev after #1368 every variant includes the echo fix. To rerun the exact commits behind the table (before = 1d4ad28, variants and breakdown without the echo fix; after = 712198c, echo fix in `tinglongwu/hymt.py`), check the commit out locally and run the CPU commands of the Hy-MT2 section below with `--src rus_Cyrl --tgt zho_Hant` / `--tgt eng_Latn --limit 200 --temp 0 --variant <card|src|q8>`, then `--breakdown OUT 40`. Use a fresh `--out` per commit and variant (the runner resumes rows already in OUT) and the same OUT for its `--score-only` step. The q8 row exists only for 712198c, which has `--fetch-q8 MODELS` (run it before translating); 1d4ad28 has `card` and `src` only.

Error breakdown of the 40 lowest-COMET sentences (share among the 40 / share among all 200):

| category | ru -> en before fix | ru -> en after fix | ru -> zh-Hant |
|---|---|---|---|
| output echoes the prompt's instruction line | (not counted yet; shows as "addition") | 0% / 0% | 0% / 0% |
| output > 1.35x reference length ("addition") | 32% / 14% | 12% / 9% | 2% / 5% |
| reference name missing from output (names / transliteration) | 42% / 36% | 48% / 36% | 15% / 8% |
| source has a named entity | 75% / 68% | 78% / 68% | 75% / 68% |
| source > 30 words | 15% / 11% | 22% / 11% | 18% / 11% |
| omission (< 0.75x reference length) | 0% / 0% | 0% / 0% | 5% / 2% |
| numbers changed | 0% / 0% | 0% / 0% | 0% / 1% |
| none of the above (wrong sense / word choice) | 28% / 52% | 38% / 56% | 70% / 78% |

- Root cause found for ru -> en: Hy-MT2 often starts its answer with the card prompt's own English instruction line, e.g. sentence 84 came back as "Note that you should only output the translated result without any additional explanation: Water ..." (also reworded as "It should be noted that only the translated result should be output, ..."). It was 7 of the 8 lowest ru -> en lines and most of the 13 "addition" lines among the lowest 40 (5 are left after the fix). The English template is used for every pair without Chinese, so X -> en / ja / ko / ru can have it too; the Chinese template (-> zh) showed no echo. Fix: `tinglongwu.hymt.strip_echo` removes a leading echoed instruction (English or Chinese form) from the output.
- After that, the lowest lines are named entities transliterated differently from the reference (Олдсмобиль Интрига -> "奧爾斯莫比爾汽車“陰謀”號" vs "奧茲摩比 Intrigue"; Ивасаки -> 伊瓦薩基 vs 岩崎), long sentences (2x over-represented) and domain word sense (cricket "калитка" = wicket translated as "gate" / "入口"). Hy-MT2 1.8B has no knowledge of these; no prompt or decoding setting tried here fixes them.

Fixes tried (ru -> zh-Hant / ru -> en, COMET / chrF / median s per sentence, CPU):

| variant | ru -> zh-Hant | ru -> en | CPU |
|---|---|---|---|
| card prompt, Q4_K_M (shipped, before) | 0.8696 / 25.14 / 1.36 | 0.8543 / 58.25 / 1.55 | EPYC 9V45 |
| prompt names the source language (`src`, before echo fix) | 0.8688 / 25.49 / 2.46 | 0.8486 / 57.90 / 2.83 | Xeon 6975P-C |
| **card prompt + echo fix (shipped by #1351)** | **0.8696 / 25.14 / 1.77** | **0.8613 / 58.51 / 1.96** | EPYC 9V74 |
| Q8_0 GGUF + echo fix (`q8`) | 0.8749 / 25.86 / 4.38 | 0.8605 / 58.40 / 5.19 | Xeon 6975P-C |
| card prompt + echo fix, rerun (baseline of the 7B run) | 0.8696 / 25.14 / 2.26 | 0.8613 / 58.51 / 2.55 | EPYC 7763 |
| opt-in Hy-MT2-7B Q4_K_M + echo fix (`7b`, `tlwu-setup --hymt7b`) | 0.8765 / 26.14 / 10.68 | 0.8759 / 62.89 / 10.47 | Xeon 6975P-C |
| Gemma 3 12B instruct Q4_K_M, plain instruction (`gemma3-12b`, bench only) | 0.8703 / 26.11 / 9.10 | 0.8745 / 63.63 / 9.22 | EPYC 9V45 |
| Qwen3 8B Q4_K_M, plain instruction, thinking off (`qwen3-8b`, bench only) | 0.8696 / 26.97 / 8.95 | 0.8691 / 62.49 / 9.45 | Xeon 6975P-C |

- Echo fix: ru -> en +0.0070 COMET (+0.26 chrF); ru -> zh-Hant identical to the last digit (greedy decoding gives the same text on every CPU, so the difference is only the fix). It changes no model call, only two regex substitutions on the output, so speed is unchanged; the s/sentence columns differ because each job got another runner CPU.
- Naming the source language: slightly worse on both (-0.0008 / -0.0057), ruled out.
- Q8_0: +0.0053 on ru -> zh-Hant, -0.0008 on ru -> en against the fixed Q4_K_M, below the +0.008 bar on both, and the file is about 1.9x the size of Q4_K_M (memory-bound on the CPU, #1198 measured the quant speeds); ruled out. Q5_K_M / Q6_K were not measured.
- Hy-MT2-7B (run [44034316922](https://github.com/sunososobro-hub/tinglongwu/actions/runs/44034316922), commit 49eb2b6, `variants=card,7b`): +0.0069 COMET / +1.00 chrF on ru -> zh-Hant and +0.0146 COMET / +4.38 chrF on ru -> en against the 1.8B in the same run. The bigger model mostly fixes names and transliteration on ru -> en ("reference name missing" among all 200: 36% -> 28%; "Jamie Sиминоff" -> "Jamie Siminoff"). It does not fix domain senses: the cricket line (калитка = wicket) is still the lowest ru -> zh-Hant line ("大門"). The 7B is about 4.6x slower per sentence on the CPU (10.7 vs 2.3 s median, on different runner CPUs), so it fails the "no more than 10% slower" bar as a default. It stays opt-in for GPUs (#279) and is the best Hy-MT2 option for Russian sources. The +0.008 COMET bar is met on ru -> en but not on ru -> zh-Hant (+0.0069).
- Not reached: COMET >= 0.88 on ru -> zh-Hant (1.8B 0.870, 7B 0.877) and ru -> en (1.8B 0.861, 7B 0.876). Even the 7B, about 4x the parameters, stays below 0.88 on both. On ru -> zh-Hant it is within 0.003 of Google Cloud Translation (0.879, full devtest). Everything tried within the Hy-MT2 family is measured: prompt, quantization, 1.8B vs 7B, and temperature (#107). The 0.88 target therefore needs a different model family, not a setting.
- Other model families (run [44050647595](https://github.com/sunososobro-hub/tinglongwu/actions/runs/44050647595), commit c1f6911, `variants=gemma3-12b,qwen3-8b`; GGUF through llama.cpp's own chat template, one-line "Translate the following Russian text into English / Simplified Chinese. Output only the translation" instruction, greedy, zh output through the same `zh_convert` as Hy-MT2): Gemma 3 12B (`unsloth/gemma-3-12b-it-GGUF`, Gemma terms) 0.8703 ru -> zh-Hant / 0.8745 ru -> en; Qwen3 8B (`Qwen/Qwen3-8B-GGUF`, Apache-2.0) 0.8696 / 0.8691. Neither beats Hy-MT2-7B on COMET, and on ru -> zh-Hant both are within 0.001 of the shipped 1.8B while being about 4x slower per sentence (9 s median). The lowest lines are the same categories as for Hy-MT2 (named entities 70-75% of the 40 lowest, wrong sense / word choice), with no prompt artifacts (additions 0-5%, no Cyrillic left). Result: no CPU-sized open model measured here (1.8B to 12B, three families) reaches COMET 0.88 on either Russian row; the shipped Hy-MT2-1.8B stays the default and the 7B stays the opt-in for Russian sources.

### Previous sentence as MT context, live (#1394)
`eval-fragments` (#1258 set, Hy-MT2-1.8B Q4_K_M, greedy, 4 vCPU GitHub runner, every line translated alone and then with the previous source line in the model card's contextual prompt, same runner for both) now has 23 self-written Russian software-talk polysemy lines (`kind: polysemy`: ветка, упал прод, поднять сервер, залить, железо, сборка, стенд, тормозит, ключ, ...; `bad` lists the wrong everyday sense, so "invented %" is the wrong-sense rate there) and a median seconds per MT call. Run [44065598131](https://github.com/sunososobro-hub/tinglongwu/actions/runs/44065598131) (commit 52ca974). Reproduce: `gh workflow run eval-fragments.yml --ref <ref>`, or locally `python -m tlwu_eval --fragment-cases eval/fragment_cases.jsonl --device cpu --out fragments.json` with `TLWU_HYMT_TEMP=0`.

| lines | n | chrF alone / with context | invented or wrong-sense % alone / with context | median s per line alone / with context |
|---|---|---|---|---|
| all (ru + en -> zh-Hant) | 79 | 26.1 / 27.3 | 10.1 / 5.1 | 0.918 / 1.129 (+23%) |
| polysemy (ru -> zh-Hant) | 23 | 19.2 / 22.8 | 21.7 / 8.7 | 0.954 / 1.254 (+31%) |
| fragment | 24 | 31.5 / 36.9 | 4.2 / 4.2 | 0.711 / 1.039 |
| idiom | 18 | 23.0 / 23.2 | 11.1 / 5.6 | 1.078 / 1.231 |
| number | 6 | 30.9 / 26.0 | 0.0 / 0.0 | 0.900 / 1.106 |
| complete | 8 | 46.1 / 42.8 | 0.0 / 0.0 | 0.887 / 1.036 |

- Quality: context helps where the issue said it would. On the polysemy lines the wrong-sense rate drops from 21.7% to 8.7% (5 of 23 lines to 2) and chrF rises 3.6; over all 79 lines invented content halves. Finished short lines (`complete`, `number`) lose 3-5 chrF with context (small n: 8 and 6 lines).
- Speed: +23% median MT time per line (+0.21 s), +31% on the polysemy lines. The context sentence lengthens the prompt the CPU has to read for every line. That is over the issue's 15% bar, so the opt-in setting (`--mt-context` + GUI checkbox) is **not shipped**. The tested pipeline wiring (previous finished line of the same speaker and language, at most one sentence, not across a 4 s pause) is kept on branch `fix/1394` at commit efb1e6c for a later attempt.
- COMET is not scored by this eval (chrF and the invented / wrong-sense rate only).
- What could bring it under 15%: a shorter context (only the last few words of the previous line), or reusing llama.cpp's prompt cache across lines (the previous line is already in the cache as the last prompt's text, but in another position). Neither is measured.

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

## Offline topic notes: default model bake-off (#1378, #595, #610)

Which small instruct model should write the offline meeting notes (`--topics MODEL.gguf`, #609)? `topic-bakeoff` run [44038256031](https://github.com/sunososobro-hub/tinglongwu/actions/runs/44038256031) (commit 6ea1591), one GitHub-hosted 4 vCPU runner per model (the runner CPU is in each row), Q4_K_M GGUFs, the app's own prompt and parser (`tinglongwu.topics`, greedy, 220 tokens). Quality: 25 fixed segments (10 zh, 10 en, 5 ru) scored on key facts present, invented numbers (a number in the notes that is not in the transcript) and output language = transcript language. Speed: one ~5-minute topic (816 words) summarized with the app's thread count (`cpus // 4` = 1 thread on 4 vCPU) at the lowest thread priority while 2 cores are kept busy like live ASR + MT. Peak RAM = the process's peak RSS minus the RSS before the model loaded (llama.cpp's mapped file plus its repacked CPU weights). Budget from the issue: < 60 s per 5-minute topic, < 3 GB RAM, subtitle probe p50 within +10%, plus the quality floor (right language in >= 90% of segments, >= 60% of key facts in every language).

| model (licence) | runner CPU | file MB | peak RAM MB | s / 5-min topic | s / short topic | zh facts | en facts | ru facts | invented numbers | right language | subtitle p50 | budget |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 (Apache-2.0) | Xeon 6975P-C | 2497 | 5066 | 218.1 | 28.5 | 0.84 | 1.00 | 1.00 | 2/25 | 24/25 | +1.9% | over: time, RAM |
| Llama-3.2-3B-Instruct (Llama 3.2) | EPYC 9V45 | 2019 | 4062 | 112.6 | 10.7 | 0.78 | 0.94 | 0.62 | 1/25 | 21/25 | +1.6% | over: time, RAM, quality |
| Qwen2.5-1.5B-Instruct (Apache-2.0) | EPYC 9V74 | 1117 | 2067 | 73.0 | 5.3 | 0.46 | 0.79 | 0.00 | 2/25 | 15/25 | +0.4% | over: time, quality |
| Llama-3.2-1B-Instruct (Llama 3.2) | EPYC 7763 | 808 | 1680 | 106.0 | 5.8 | 0.42 | 0.88 | 0.20 | 3/25 | 15/25 | -2.1% | over: time, quality |
| gemma-3-1b-it (Gemma) | Xeon 6975P-C | 806 | 1219 | 82.4 | 8.8 | 0.14 | 0.72 | 0.00 | 4/25 | 11/25 | +1.4% | over: time, quality |

What this shows:
- No candidate meets the CPU budget. The 1-1.5B models are fast enough per short topic but write Chinese and Russian meetings up in English (10-14 of 25 segments in the wrong language, ru key facts 0.00-0.20), so their notes are not usable for zh / ru users. Only the 3-4B models keep the transcript's language and its facts.
- Default model: **Qwen3-4B-Instruct-2507 Q4_K_M** (best quality: 24/25 in the right language, every en / ru key fact, 0.84 zh; Apache-2.0; pinned in `tinglongwu_setup/manifest.json` as `topics-qwen3-4b-2507`). On a 4 vCPU CPU with the app's single summarizer thread it needs about 3.6 minutes per 5-minute topic and ~5 GB RAM, so topics queue up during a meeting and finish after it. Recommendation: on by default only with a GPU; on CPU-only PCs an opt-in, with the notes arriving after the meeting.
- Timings move with the runner CPU (gemma-3-1b: 40-82 s per 5-minute topic across three runs of the same code), so compare the s / topic columns within ~2x, not to the second. The synthetic subtitle probe (SHA-256 work standing in for ASR/MT, not the real pipeline) stays within +2% for every model; the real ASR/MT path was not measured here.

Reproduce: dispatch `topic-bakeoff` (input `candidates`, empty = all), or locally `python scripts/topic_bakeoff.py --fetch qwen3-4b-2507 --dir cand` then `python scripts/topic_bakeoff.py --model qwen3-4b-2507=cand/qwen3-4b-2507/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --out r.json`; the per-segment notes are in the job log.
