# Built-in term domains

tinglongWu ships term tables that tell the translator how a field's terms are said in each language (for example
`прошивка` -> `韌體`, `firmware` -> `ファームウェア`). They live in `tinglongwu/data/terms/`, **one file per domain**.
A domain is only used when its scenario is on, so engineering terms do not change an everyday conversation.

Adding a domain = adding one file. No Python change is needed: the loader finds every `*.tsv` in that folder.

## File format

UTF-8, one file per domain, named `<domain>.tsv` (lower case, `a-z 0-9 _ -`, e.g. `networking.tsv`).

```
# scenario: tech
# Any other line starting with # is a comment.
id	en	ru	ja	ko	zh-TW	zh-CN
net.subnet_mask	subnet mask	маска подсети | маску подсети	サブネットマスク	서브넷 마스크	子網路遮罩	子网掩码
```

- **Line 1** is `# scenario: <key>`: the scenario this domain belongs to (engineering files use `tech`). Several keys
  may be listed with commas; `*` means "always on".
- The **header** is the first line that is not a comment. Columns are separated by one TAB: `id`, then the
  languages `en ru ja ko zh-TW zh-CN`. More language columns (e.g. `vi`) may follow; they work without code changes.
- **id**: a lower-case ascii slug, unique across all files, ideally with a domain prefix (`net.subnet_mask`).
- **Cells** may hold several spellings separated by ` | `. The first one is the main spelling, used as the
  translation when that language is the target. All spellings are matched when it is the source. List common slang,
  loanword forms, and for Russian the case forms of short words. An empty cell means the language has no term.
- A spelling starting with `*` is **target-only**: it is used as a translation but never matched in speech. Use it
  for a correct translation that is also an everyday word (zh `*提交`, ko `*기능`, en `*feature`).

How matching works: Russian sources match their own noun case forms and verb forms. Japanese, Korean and Chinese
sources match anywhere in the sentence and need at least 2 characters. Other languages match by word prefix.

## Rules for entries

- Generic, publicly common terms only. No brand, vendor, company or product names, and nothing that points to a
  specific employer or customer.
- Write the entries yourself. Do not copy or scrape another glossary or dictionary (license).
- Leave out words with a common everyday sense in any language (image, library, branch, thread, задача, сеть, 核心)
  unless you mark that spelling target-only with `*`. When in doubt, leave it out: a wrong hit changes an ordinary
  sentence.
- zh-TW uses Taiwan terms (程式, 韌體, 記憶體, 網路, 介面, 伺服器); zh-CN uses Mainland terms (程序, 固件, 内存, 网络, 接口, 服务器).
- One concept per row; the same spelling must not appear twice in one language column of a file.

## Check before you open a PR

```
python scripts/terms_check.py tinglongwu/data/terms/<domain>.tsv
```

It checks line 1, the header, the cell count of every row, ids (also against the shipped files), empty rows, the
cell format and repeated spellings. The CI `test` job runs the same check on every shipped file
(`tests/test_term_table_1689.py`), so a broken file fails the PR.

## How the app uses it

`glossary.load_builtin_pair(src, tgt, domains)` builds the `source term = target term` list for any language pair;
`glossary.scenario_domains(key)` lists the domains of a scenario. On the command line, `--builtin-glossary
--term-scenario tech` turns the `tech` domains on for the `--lang` -> `--to` pair. User glossary entries always win.
