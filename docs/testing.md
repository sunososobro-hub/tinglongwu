# Tests

Moved from the README (#342). See also [CONTRIBUTING.md](../CONTRIBUTING.md#running-tests).

`pip install -r requirements-dev.txt && pytest` (no models or network needed).

## Eval merge gate (#361)

`python -m tlwu_eval --gate REPORT.json --baseline eval/baseline.json` compares a smoke report to the committed
baseline and exits 1 when any pair's chrF is below `chrf - chrf_margin` or its MT median is above
`mt_median_s_max` (0 otherwise, 2 for a missing or invalid file). It prints a Markdown table (chrF x100 to two
decimals, delta, MT median, PASS/FAIL/SKIP per pair) that can be appended to `$GITHUB_STEP_SUMMARY`. A pair with
no clips in the report is SKIP. Every margin and budget lives in `eval/baseline.json`, none in code; a pair whose
`chrf` is `null` (not measured yet) is gated on MT latency only.

End to end on a CPU (Hy-MT2 1.8B Q4 via `tlwu-setup --hymt`, Whisper base or small; needs the smoke data folder):

```
python -m tlwu_eval --smoke --data eval_data/smoke --clips 5 --model small --engine hymt2-cpu --device cpu --out r.json
python -m tlwu_eval --gate r.json --baseline eval/baseline.json
```

The smoke data folder is not in the repo. `python scripts/bench/make_ci_smoke.py --out eval_data/smoke` (#569)
rebuilds an open-licensed one on any machine with network: 5 FLEURS test clips (CC-BY 4.0) each for en, ja, ru and
zh, pinned by dataset revision and per-WAV sha256 in `eval/ci_smoke.json` (exit 1 on a mismatch; about 14 MB
downloaded).

To record a baseline, copy each pair's `chrf` from `r.json` into `eval/baseline.json` and set `chrf_margin`.

## Hy-MT2 new languages: Experimental vs supported (#472)

Add 5 clips each for it, pt, vi, th and id to the smoke manifest (with `ref_zh`), install `unbabel-comet`, then:

```
python -m tlwu_eval --smoke --data eval_data/smoke --clips 5 --engine hymt --langs it,pt,vi,th,id --comet --out r472.json
```

It prints a table with COMET x100 (two decimals) per language. The floor, set in #472 before any run, is 75.00: a
language at or above it may leave `langs.EXPERIMENTAL` (and join the README and roadmap tables); below it, it stays
Experimental. Paste the table into the PR that moves the languages.

## Silence / noise hallucination (#121)

`--smoke` also feeds four no-speech categories (silence, aircon, keyboard, distant babble; synthetic, seeded) to the ASR and prints the count and rate (x100, 2 decimals) per category with any text that would still be shown. Any count > 0 is red and fails the smoke. Put real recordings in `<dir>/{silence,aircon,keyboard,distant}/*.wav` (`--noise-dir` or `$TLWU_EVAL_NOISE_DIR`, outside the repo) to add them. `--no-noise` skips it.
