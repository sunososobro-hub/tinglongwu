# eval/

Committed inputs of the eval harness (`python -m tlwu_eval`, see [docs/testing.md](../docs/testing.md)).

| File | What |
| --- | --- |
| `baseline.json` | merge gate baseline for `--gate` (#361, #570): chrF and MT budget per pair |
| `ci_smoke.json` | pinned FLEURS clips for the CI smoke set (#569) |
| `lowend_baseline.json` | low-end CPU gate fallback (#750) |
| `baseline_ko.json`, `ko_pins.json`, `tier2_*.json` | Korean and tier-2 runs |
| `user_cases.jsonl` | text cases from real `translation-error` reports (#679), created by the first case |

## Cases from real translation errors (#679)

Every real translation error a user reports should become a regression case, so the eval set grows from real
use and not only from FLORES.

One case per line in `eval/user_cases.jsonl`:

```json
{"id": "issue-900", "pair": "ja->zh", "src": "...", "ref": "...", "tlwu": "...", "target": "zh-TW", "source": "https://github.com/sunososobro-hub/tinglongwu/issues/900"}
```

`pair` uses the gate's pair keys (`baseline.json`), `ref` is the reporter's expected translation, `tlwu` the
wrong output they saw, `target` the target variant when it is not the base code, `source` the issue URL. The cases are text only (no audio): the smoke gate does not score them yet; that
needs an MT-only pass (follow-up issue).

Add one report:

```
python scripts/add_eval_case.py 900              # fetches issue #900 with gh api
python scripts/add_eval_case.py --issue-json issue.json   # offline, a saved issue
```

The issue must come from the "Translation error" form. In "What happened" write the source after `Source:` and
the tlwu output after `Output:` (or two paragraphs: source, then output). A case whose issue URL or whose
`pair` + `src` is already in the file is skipped, so running the script twice adds nothing. An unusable issue
exits 2 with the reason and changes nothing.

### Weekly loop

1. The owner reviews closed `translation-error` issues and labels the real, correct ones `eval-case`.
2. Once a week S-hard runs `python scripts/add_eval_case.py --label eval-case`. It reads every closed issue with
   that label; issues already converted are skipped as duplicates, so the step is safe to repeat.
3. If `eval/user_cases.jsonl` changed, S-hard opens a PR from `fix/<newest issue>` (`Refs #<newest issue>`) with only that file.
   An issue that exits 2 gets a 1-line comment with the reason instead.

Verify: `python -m pytest -q tests/test_add_eval_case.py`.
