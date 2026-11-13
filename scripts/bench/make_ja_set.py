#!/usr/bin/env python3
"""Dev tool (#346 acceptance 1, #429): build the Japanese ja -> zh-TW eval set for `tlwu-eval` from FLEURS +
FLORES-200 (read news, style=news) and Tatoeba (conversational dialogue, style=dialogue).

  python scripts/bench/fetch_data.py --out DATA --fleurs ja_jp --tatoeba-ja 40   # network
  python scripts/bench/make_ja_set.py --data DATA --out eval_data/ja  # offline from here on
  python -m tlwu_eval --smoke --data eval_data/ja --per-lang 60 --out ja.json

FLEURS reads FLORES sentences aloud, so each FLEURS ja_jp utterance is matched to FLORES-200 jpn_Jpan (dev+devtest) by
normalised text and gets the human zho_Hant translation of the same FLORES line as its zh-TW reference.
One utterance per distinct sentence, in test.tsv order; unmatched utterances are dropped and counted.
Dialogue (#429): Tatoeba Japanese sentences (short spoken-style lines: requests, questions, casual speech) that have a
native-speaker recording under a licence in DIALOGUE_LICENSES and a human Mandarin (cmn) translation; the cmn text is
converted to Taiwan Traditional script (the app's zh_convert "tw") for ref_zh. Ordered by sentence id.
Writes OUT/manifest.jsonl (the tlwu_eval format: id, lang, wav, ref_text, ref_zh + style, source), the audio copies
and OUT/LICENSE.txt. Licences: FLEURS CC-BY 4.0 (audio + transcript), FLORES-200 CC-BY-SA 4.0 (zho_Hant reference),
Tatoeba sentences CC-BY 2.0 FR, Tatoeba audio per clip (listed in LICENSE.txt with the speaker).
Exit 1 when fewer than --min news sentences or --min-dialogue dialogue clips are found.
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

TATOEBA_LICENSE = """
Dialogue clips (style=dialogue, #429): Tatoeba, https://tatoeba.org
  Japanese sentences and their Mandarin translations: CC-BY 2.0 FR (https://creativecommons.org/licenses/by/2.0/fr/);
  the Mandarin translation is converted to Taiwan Traditional script for ref_zh.
  Audio: recorded by Tatoeba contributors, each clip under the licence listed below (unmodified mp3).
"""

# Tatoeba audio licences that allow copying a clip into a local eval set. CC BY-NC 4.0 allows it for non-commercial
# use (an evaluation set, never shipped or sold); ND and unlicensed recordings are left out.
DIALOGUE_LICENSES = ("CC0 1.0", "CC BY 4.0", "CC BY-SA 4.0", "CC BY-NC 4.0")
TATOEBA_DIR = "tatoeba_ja"


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


def _tsv(path: Path):
    for line in path.read_text("utf-8").splitlines():
        c = line.split("\t")
        if len(c) >= 2:
            yield c


def tatoeba_candidates(root: Path) -> list[dict]:
    """Japanese Tatoeba sentences with an allowed-licence recording and a cmn translation, by sentence id.
    Reads ROOT/tatoeba_ja/{sentences_with_audio.csv, jpn_sentences.tsv, cmn_sentences.tsv, jpn-cmn_links.tsv}
    (Tatoeba export formats). One recording (lowest audio id) and one translation (lowest id) per sentence."""
    d = root / TATOEBA_DIR
    ja = {c[0]: c[2].strip() for c in _tsv(d / "jpn_sentences.tsv") if len(c) >= 3 and c[2].strip()}
    cmn = {c[0]: c[2].strip() for c in _tsv(d / "cmn_sentences.tsv") if len(c) >= 3 and c[2].strip()}
    links = {}
    for c in _tsv(d / "jpn-cmn_links.tsv"):
        if c[0] in ja and c[1] in cmn:
            links.setdefault(c[0], []).append(c[1])
    audio = {}
    for c in _tsv(d / "sentences_with_audio.csv"):
        lic = c[3].strip() if len(c) > 3 else ""
        if c[0] in links and lic in DIALOGUE_LICENSES and c[1].strip().isdigit():
            audio.setdefault(c[0], []).append((int(c[1]), c[2].strip(), lic))
    out = []
    for sid in sorted(audio, key=int):
        aid, user, lic = min(audio[sid])
        out.append({"sentence": sid, "audio": str(aid), "speaker": user, "license": lic, "ja": ja[sid],
                    "cmn": cmn[min(links[sid], key=int)]})
    return out


def select_dialogue(root: Path, n: int) -> list[dict]:
    """Up to n candidates whose recording was downloaded (ROOT/tatoeba_ja/audio/<audio id>.mp3), with ref_zh set.
    [] when no Tatoeba export was fetched."""
    from tinglongwu.translate import zh_convert

    if not (root / TATOEBA_DIR / "sentences_with_audio.csv").is_file():
        return []
    rows, seen = [], set()
    for c in tatoeba_candidates(root):
        mp3 = root / TATOEBA_DIR / "audio" / f"{c['audio']}.mp3"
        key = norm(c["ja"])
        if not mp3.is_file() or key in seen:
            continue
        seen.add(key)
        rows.append({**c, "path": mp3, "ref_zh": zh_convert(c["cmn"], "tw")})
        if len(rows) >= n:
            break
    return rows


def write_set(rows, out: Path, dialogue=()) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, (wav, transcript, _flores_ja, zh) in enumerate(rows, 1):
        name = f"ja_fleurs_{i:03d}.wav"
        shutil.copyfile(wav, out / name)
        manifest.append({"id": f"ja_fleurs_{i:03d}", "lang": "ja", "wav": name, "ref_text": transcript,
                         "ref_zh": zh, "style": "news", "source": f"fleurs:ja_jp/test/{wav.name}"})
    credits = []
    for i, d in enumerate(dialogue, 1):
        name = f"ja_dialogue_{i:03d}.mp3"
        shutil.copyfile(d["path"], out / name)
        manifest.append({"id": f"ja_dialogue_{i:03d}", "lang": "ja", "wav": name, "ref_text": d["ja"],
                         "ref_zh": d["ref_zh"], "style": "dialogue", "license": d["license"],
                         "source": f"tatoeba:sentence/{d['sentence']}/audio/{d['audio']}"})
        credits.append(f"  {name}: sentence https://tatoeba.org/sentences/show/{d['sentence']}, audio {d['audio']} "
                       f"by {d['speaker']}, {d['license']}\n")
    (out / "manifest.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in manifest), "utf-8")
    (out / "LICENSE.txt").write_text(LICENSE_TEXT + (TATOEBA_LICENSE + "".join(credits) if credits else ""), "utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True, help="fetch_data.py output (flores200_dataset/, fleurs_ja/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=60, help="sentences to keep (default 60)")
    ap.add_argument("--min", type=int, default=50, help="fail when fewer matched (default 50, #346)")
    ap.add_argument("--dialogue", type=int, default=30, help="Tatoeba dialogue clips to keep (default 30, #429)")
    ap.add_argument("--min-dialogue", type=int, default=20, help="fail when fewer dialogue clips (default 20, #429)")
    a = ap.parse_args(argv)
    rows, dropped = select(Path(a.data), a.n)
    dialogue = select_dialogue(Path(a.data), a.dialogue) if a.dialogue > 0 else []
    write_set(rows, Path(a.out), dialogue)
    print(f"ja set: {len(rows)} news sentences + {len(dialogue)} dialogue clips -> {Path(a.out) / 'manifest.jsonl'} "
          f"(unmatched dropped: {dropped})")
    rc = 0
    if len(rows) < a.min:
        print(f"make_ja_set: only {len(rows)} matched sentences, need >= {a.min}", file=sys.stderr)
        rc = 1
    if len(dialogue) < a.min_dialogue:
        print(f"make_ja_set: only {len(dialogue)} dialogue clips, need >= {a.min_dialogue} "
              "(fetch them with fetch_data.py --tatoeba-ja 40)", file=sys.stderr)
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
