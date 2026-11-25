# S-split: split-and-route (Opus, every 2 h at :28)

Read after `common.md`. For routing, this file wins over tlwu_rules; for everything else the rules win. Writes no product code, opens no PR, merges nothing, never touches `.github/`. Goal: the Sonnet pickup pool holds only issues a cloud Sonnet worker can really finish. Background (2026-11-24): workers idled hourly because nearly all 28 todo issues were undoable (needed `.github/`, GUI rewrites > 300 lines, real devices or special environments) and skips left no label behind.

1. Inventory: open issues (not PRs) with `status:todo` + `ok-to-work` and none of `needs-owner` / `needs-opus` / `status:blocked` / `status:proposal` / `status:frozen` / `需實測`. `pool_before` = those without `model:opus`.
2. Route: at most 15 per run, P smallest then number smallest. Skip one that already has your `route:` comment in the last 24 h with no newer comment. Read body + all comments (later owner comments win); read the related source when you need to estimate size. Pick one:
   a) Needs `.github/`, workflows or runner settings -> add `model:opus` (the Opus worker does these; Opus PASS merges workflows, rules §3 / §14). Needs `.claude/`, `LICENSE`, `SECURITY.md`, permissions or secrets -> `needs-owner` + one-line question and recommendation.
   b) Estimated > 300 changed lines -> split into child issues of <= 300 lines that merge independently: same English title prefix, first line `Split from #N.`, body with `Goal:` / `Acceptance:` (measurable) / `Out of scope:` / `Verify:`; labels `src:ai ok-to-work status:todo` + the parent's P, type, `area:*`, `mode:*` + `model:sonnet` (`model:opus` only for concurrency / audio routing / translation quality). Parent: remove `status:todo`, add `status:blocked`, comment `split into #a #b …`. Search `state=all` first to avoid duplicates.
   c) Needs a real device, real call, GPU, emulator or data the cloud lacks (e.g. Whisper + FLEURS eval) to build or verify -> add `需實測`, remove `ok-to-work`. If part of it is cloud-doable, split that part out first per b).
   d) Nothing left to do (the failure no longer happens, another PR did it) -> comment the reason and close.
   e) A cloud Sonnet worker can finish it -> leave the labels.
   Every case except e) gets one comment `route: <split|opus|needs-test|close|owner> — <reason>`.
3. Restock: recount `pool_after`. If < 5, open bug / test / eval issues per the rules restock rule (evidence only, duplicates checked, the four-line format, labels as in b) until 5, at most 5. No new features, no padding.
4. Log one line on #61: `split-check: pool_before=<n> pool_after=<n> split=<n> opus=<n> needtest=<n> owner=<n> closed=<n> added=<n>`.
5. Report (last line): `✅ 收工 可做單 <before>→<after> 拆#… 轉opus#… 需實測#… 關#… 補#…`
