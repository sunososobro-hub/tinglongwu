## 4. Model choice
- Opus only for concurrency / threading, audio routing / devices, translation quality (MT / ASR evals); everything else Sonnet.
- A Sonnet attempt that fails once (PR closed, or CI red twice): relabel `model:opus` and retry as `fix/N-r2`.
