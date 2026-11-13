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

To record a baseline, copy each pair's `chrf` from `r.json` into `eval/baseline.json` and set `chrf_margin`.
