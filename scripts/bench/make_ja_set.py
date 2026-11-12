#!/usr/bin/env python3
"""Dev tool (#346 acceptance 1): build the Japanese ja -> zh-TW eval set for `tlwu-eval` from FLEURS + FLORES-200.

  python scripts/bench/fetch_data.py --out DATA --fleurs ja_jp      # FLORES-200 + FLEURS ja_jp test (network)
  python scripts/bench/make_ja_set.py --data DATA --out eval_data/ja  # offline from here on
  python -m tlwu_eval --smoke --data eval_data/ja --per-lang 60 --out ja.json

FLEURS reads FLORES sentences aloud, so each FLEURS ja_jp utterance is matched to FLORES-200 jpn_Jpan (dev+devtest) by
normalised text and gets the human zho_Hant translation of the same FLORES line as its zh-TW reference.
One utterance per distinct sentence, in test.tsv order; unmatched utterances are dropped and counted.
Writes OUT/manifest.jsonl (the tlwu_eval format: id, lang, wav, ref_text, ref_zh + source), the wav copies and
OUT/LICENSE.txt. Licences: FLEURS CC-BY 4.0 (audio + transcript), FLORES-200 CC-BY-SA 4.0 (zho_Hant reference).
Exit 1 when fewer than --min sentences matched.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

LICENSE_TEXT = """Japanese eval set (ja -> zh-TW) for tlwu-eval, built by scripts/bench/make_ja_set.py (#346).
Audio and ja transcripts: FLEURS ja_jp test split, Google, CC-BY 4.0
  https://huggingface.co/datasets/google/fleurs
zh-TW references: FLORES-200 zho_Hant (dev+devtest), Meta, CC-BY-SA 4.0
  https://github.com/facebookresearch/flores
Each manifest row's "source" field names the FLEURS file it came from.
"""


def norm(t: str) -> str:
    """NFKC, lower case, letters/digits only, no whitespace (Japanese has no word spaces; FLEURS may add some)."""
    t = unicodedata.normalize("NFKC", t).lower()
    return re.sub(r"\s+", "", "".join(c for c in t if c.isalnum()))


def load_flores(root: Path, src: str = "jpn_Jpan", ref: str = "zho_Hant") -> dict:
    d = root / "flores200_dataset"
    out = {}
    for split in ("dev", "devtest"):
        a = (d / split / f"{src}.{split}").read_text("utf-8").split("\n")
        b = (d / split / f"{ref}.{split}").read_text("utf-8").split("\n")
        if len(a) != len(b):
            raise ValueError(f"FLORES {split}: {src} has {len(a)} lines, {ref} has {len(b)}")
        for s, r in zip(a, b):
            if s.strip() and r.strip():
                out.setdefault(norm(s), (s.strip(), r.strip()))
    return out


def select(root: Path, n: int, fleurs_dir: str = "fleurs_ja"):
    """[(wav_path, fleurs_transcript, flores_ja, flores_zh_hant)], unmatched_count. Missing audio files count as unmatched."""
    flores = load_flores(root)
    fdir = root / fleurs_dir
    audio = {p.name: p for p in (fdir / "audio").rglob("*.wav")}
    rows, seen, dropped = [], set(), 0
    for line in (fdir / "test.tsv").read_text("utf-8").splitlines():
        c = line.split("\t")
        if len(c) < 3:
            continue
        key = norm(c[2])
        if not key or key in seen:
            continue
        if key not in flores or c[1] not in audio:
            dropped += 1
            continue
        seen.add(key)
        rows.append((audio[c[1]], c[2].strip(), *flores[key]))
        if len(rows) >= n:
            break
    return rows, dropped


def write_set(rows, out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, (wav, transcript, _flores_ja, zh) in enumerate(rows, 1):
        name = f"ja_fleurs_{i:03d}.wav"
        shutil.copyfile(wav, out / name)
        manifest.append({"id": f"ja_fleurs_{i:03d}", "lang": "ja", "wav": name, "ref_text": transcript,
                         "ref_zh": zh, "source": f"fleurs:ja_jp/test/{wav.name}"})
    (out / "manifest.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in manifest), "utf-8")
    (out / "LICENSE.txt").write_text(LICENSE_TEXT, "utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True, help="fetch_data.py output (flores200_dataset/, fleurs_ja/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=60, help="sentences to keep (default 60)")
    ap.add_argument("--min", type=int, default=50, help="fail when fewer matched (default 50, #346)")
    a = ap.parse_args(argv)
    rows, dropped = select(Path(a.data), a.n)
    manifest = write_set(rows, Path(a.out))
    print(f"ja set: {len(manifest)} sentences -> {Path(a.out) / 'manifest.jsonl'} (unmatched dropped: {dropped})")
    if len(manifest) < a.min:
        print(f"make_ja_set: only {len(manifest)} matched sentences, need >= {a.min}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
