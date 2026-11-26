# Decisions

- 2026-11-10 (owner): work-mix ratio of worker minutes is fix 30% / new features 30% / QA 15% / eval 10% / ideas 5% / docs 5% / maintenance 5%. Since replaced, see the background list below.
- 2026-11-13 (owner): pipeline redesign - throttle faucet issue #574 (`level: N`, 0-4), slots A/B/C + W-hard, triage split from work, hourly 48 h ledger on #61, deficit-based picker, evidence-only supply, area lock. See `.agent/tlwu_rules.md` section 10.
- 2026-11-13 (owner, #596): language priority follows the market, not a fixed list. Tier 1: en, ja, ko (with zh). Tier 2: vi, th, id, es, de, fr. Everything else best-effort; Russian stays supported but is no longer a priority. Source: `language_priority` in `docs/support.json`. The support table lights only from real runs (no green without evidence).

## Agent pipeline rules: background (moved out of `.agent/` by #1856)
The rules in `.agent/tlwu_rules.md`, `.agent/rules_release.md` and `.agent/rules_patrol.md` state only what holds today; this list keeps the why. Section numbers refer to those files.

- 2026-11-13 (owner): the rules override any routine prompt where they conflict (section header).
- 2026-11-14 (owner, #808): the home-PC worker lane is retired; cloud workers take `os:windows` / `local-only` issues (section 1).
- 2026-11-14 (owner, #831): g4 / g5 narrowed. Of the last 200 PRs, 73 went to Opus review and only 5 (7%) got CHANGES; median wait 41 min. Paperwork is not a FAIL, partial PRs merge, the FAIL reasons list, and a FAIL / CHANGES keeps the PR open for the next worker (sections 2, 3, 14).
- #742: an Opus PASS is honoured by mergers; g4 ignores `tests/`; platform-only `skipif` is not a skip; Opus reviewers take up to 4 items per run. Open PRs used to lock their area: they locked 5 areas and left 4 of 22 items claimable, so open PRs no longer lock (sections 3, 6, 10).
- #1473 / #1580: `merge-gate.yml` honours head-bound PASS lines; a PASS naming a full SHA survives later pushes that touch no protected / g4 path (sections 3, 17).
- 2026-11-17 (owner, #1412): check CI once, never poll, because each poll resends the whole context (token cost) (section 3).
- 2026-11-19 (owner, #1607): one `sleep 270` after opening a PR, because CodeRabbit's median review time is about 5 min (section 3).
- 2026-11-19 (owner, #1579): "主要是審核太囉嗦吧". #1546 ran 5 CodeRabbit rounds in an hour; of 17 PRs with Minor-only findings, 11 still got 1-5 fix pushes (#1522: 4 rounds). Hence one review round and the 400-line PR size target (section 3).
- #944, #1443, #1309 (owner: "這很嚴重的，改別人家設定"): a checked-in `.claude` SessionStart hook rewrote contributors' global git identity and `core.hooksPath`. Identity is set only in cloud sessions and repo code never writes outside the checkout; #1456 and #1678 added the hand-run installer's PATH line and app shortcuts (sections 2, 18).
- #566: dirty PRs are resolved in place; pickers avoid files open PRs touch (sections 7, 8).
- #1470: the `orphan-sweep` Action took over the timeout sweep from the agents; doing -> PR p90 is 27 min, hence 20 / 45 min (section 6).
- 2026-11-13 (owner): throttle faucet #574, slots, deficit-based picker, evidence-only supply, area lock (section 10).
- 2026-11-14 (owner, #785): the picker block holds against older routine prompts ("do 1 item", "doing >= 6", "45 min") (section 10).
- 2026-11-16 (owner, #1274): `area:ci` / release issues are P1 only while dev `test` is red or a release PR is stuck > 6 h; owner field-test bugs are P1 (section 10).
- 2026-11-18 (owner: no AI): triage by a GitHub Action. The W-easy routine was retired: AI-opened issues did not wake it and one AI run per issue cost too much (patrol section 10).
- #1469: the hourly `ledger` Action posts the ledger and infra line; S-hard no longer does. Targets from #574 line 3 (owner 2026-11-18; infra added 2026-11-20). The 2026-11-20 work-mix decision (fix 25 / features 30 / infra 10 / QA 10 / eval 10 / ideas 5 / docs 5 / maint 5) and the older 1.3x skip rule and 00:28 7-day ratio check are carried by the ledger deficit picker (patrol section 10).
- 2026-11-20 (owner): `src:owner` work is exempt from the mix and the infra cap (sections 10, 13).
- 2026-11-25 (owner): area lock raised from 1 to 2 `status:doing` per area, because one lock per area turned `area:mobile` into a single lane; requeue of red / dirty PRs by `board-scan` (section 10).
- #1581: review-fix minutes are logged as `phase=fix` comments (section 10).
- 2026-11-13 (owner, #585): "聽 說 雙向是一個維度，平台是一個維度，語言是一個" - mode / platform / language labels; #662 added `lang:*` / `os:*` triage (patrol section 12).
- 2026-11-14 (owner): infra cap, "我衝到最後會拉不回來" (section 13).
- 2026-11-13 (owner, #588): auto-release safety and the issue format; fork PRs are treated like worker PRs with no owner click (owner amendment 2026-11-13) (sections 14, 15).
- 2026-11-15 (owner, #976): "我是只想要維護一個main，這個分支要有品質，但同時也走不需要品質的開發，至少我自己可以快速看到我要的東西，到時候回去main再卡關" - `dev` fast lane, `main` quality lane (section 17).
- 2026-11-20 (owner "a1", #1687): `mini-eval` gates ASR / MT / eval PRs; a single culprit commit for a red REQUIRED_CHECKS run is reverted first. #1618: `mobile-apk` gates `mobile/**` PRs (sections 14, 17).
- 2026-11-13 (owner): "Important things can't wait for a release." - docs fast lane to main (release section 11).
- 2026-11-14 (owner, #848): hw-tests deferred when no runner starts within 30 min (release section 14).
- 2026-11-19 (owner, #1596): "mac壞了不影響 windows" - per-platform release lanes (release section 14).
- 2026-11-14 (owner): "到啥就 release 啥，大功能才進版" - patch bumps by cadence; 2026-11-16 18:22 (owner, #1274): twice a day (was hourly). #1267: the bump may carry its `docs/whats_new.md` section; 2026-11-16 (owner): such a bump needs no Opus PASS (release section 14).
- #976 / #953: the release snapshot branch made the dev release freeze unnecessary (release section 14).
- #950 / #956 / #957 / #959: hotfix lane (release section 14). #589: `auto-release.yml`.
- #681: hw-tests nightly and flaky-check closing (patrol section 16).
- 2026-11-18 (owner, #1471): the patrol reads the `[board]` line first (patrol section 19).
- 2026-11-19 (owner, #1607): #1247's 10 s subtitle-gap limit was never calibrated (1 of 15 runs under it); #1247, #1369 and #1562 each reached fix round 3+ on a timing check and were frozen. Hence baseline-first timing checks and the round-2 stop (section 20).
- 2026-11-25 (owner): "瘦身一下吧，這樣我才敢造很多 scheduler" - routine prompts keep only the step-0 gate; procedures live in `.agent/routines/`.
- 2026-11-25 (owner, #1856): the rules file was 53 KB loaded on every wake; it was split by role and rewritten as current state only. Finished transitions dropped then: agents running the orphan sweep until the Action had a scheduled run on `main`; "until `auto-release.yml` exists on dev"; the legacy `dev -> main` release PR and its `release-fix` freeze; the out-of-repo `prompt.md` note.
- 2026-11-25 (owner, #1875 C6, #1887): the per-PR file listing (section 8), the area lock and the `status:doing` <= 10 cap are dropped; throttle #574 is the only brake. The author merges `origin/dev` before its final test run and resolves conflicts itself (architect review: 1+N file listings per claim bought nothing when the answer was "edit anyway"; conflicts left for later landed on cold sessions).
- 2026-11-25 (owner, #1918): protected / g4 paths and per-folder area / model / review defaults moved from hard-coded lists in `merge_gate.py` into per-folder `OWNERS` files (`scripts/owners.py`); protection is the union along the folder chain, so a nested OWNERS can never unprotect, and the gate reads the dev checkout, never the PR.
