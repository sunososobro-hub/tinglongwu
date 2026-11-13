# Decisions

- 2026-11-10 (owner): work-mix ratio of worker minutes is fix 30% / new features 30% / QA 15% / eval 10% / ideas 5% / docs 5% / maintenance 5%. See `.agent/tlwu_rules.md` section 5.
- 2026-11-13 (owner): pipeline redesign - throttle faucet issue #574 (`level: N`, 0-4), slots A/B/C + W-hard, triage split from work, hourly 48 h ledger on #61, deficit-based picker, evidence-only supply, area lock. See `.agent/tlwu_rules.md` section 10.
- 2026-11-13 (owner, #596): language priority follows the market, not a fixed list. Tier 1: en, ja, ko (with zh). Tier 2: vi, th, id, es, de, fr. Everything else best-effort; Russian stays supported but is no longer a priority. Source: `language_priority` in `docs/support.json`. The support table lights only from real runs (no green without evidence).
