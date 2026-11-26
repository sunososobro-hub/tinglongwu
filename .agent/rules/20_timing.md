## 20. Timing checks
- A timing check (anything measured in seconds on a real run) becomes acceptance only after >= 10 baseline runs of the current build; its limit is the baseline p95 plus a stated margin, with the run links in the PR.
- Acceptance is written `passes in N of M runs` (e.g. 9 of 10), never a single run or `k in a row`.
- A round is a PR that changes behaviour to make the check pass (product code, the check's logic or limit); diagnosis-only PRs are not rounds.
- Still red after round 2: label `flaky-check` and stop fixing. The only next PR turns it into a deterministic test or a report-only step, and only with run links showing it is also red on a build without the change under test; the value stays in the step summary and a deterministic test covers the behaviour. Without that evidence -> `status:frozen`. Any other issue reaching round 3 -> `status:frozen`.
- Never change a product default (threshold, timeout, model setting) to make a test pass without A/B data in the PR.
