# Releasing: Step 6: support tables and contributors

Part of [RELEASING](../RELEASING.md).

6. Before the bump, update the single source of the support tables and the contributors block: `docs/support.json` (lights, legend, download hints) and `.all-contributorsrc` (people). Run `python scripts/render_support.py` to rewrite the contributors row (`<!-- CONTRIBUTORS:START -->` ... `<!-- CONTRIBUTORS:END -->`) in the three READMEs and the dot grid on the roadmap page; CI fails (`tests/test_render_support.py`) if they differ from the source. The support tables appear only in the release notes and on the roadmap. Rules for the source:

   - Device table: only mark 🟢 what someone ran on a real machine (or in the release gate) for this release.
   - Language table: rows are languages by native name plus code (`中文 (zh)`, `English (en)`, `日本語 (ja)`, `Русский (ru)`, `한국어 (ko)`), no flag emoji anywhere (flags map languages to countries). Chinese is one row (speech has no script); Traditional and Simplified are output columns. If a light holds on one OS only, say so in the cell (`🟢 Windows`). Only list languages this tag supports.
   - Contributors: all-time and cumulative (reporters included; the list only grows). "N people" counts humans only; AI tools get their own line, as text links, no logos. The block has no `@` text: an @mention notifies the person, so mentioning everyone on every release is spam.
