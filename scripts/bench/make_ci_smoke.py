#!/usr/bin/env python3
"""Dev tool (#569, #340 part 2): rebuild the `tlwu_eval --smoke` set (eval_data/smoke) from open-licensed audio.

  python scripts/bench/make_ci_smoke.py --out eval_data/smoke     # network (huggingface.co), < 500 MB download
  python -m tlwu_eval --smoke --data eval_data/smoke --out smoke.json

Source: FLEURS test split (google/fleurs, CC-BY 4.0), pinned to the dataset revision in eval/ci_smoke.json together
with each clip's FLEURS file name and the sha256 of its WAV, so every machine gets byte-identical clips. A WAV whose
sha256 differs from the pin is reported and the script exits 1 without writing manifest.jsonl.
FLEURS reads FLoRes sentences aloud; column 0 of test.tsv is the FLoRes id, shared by every FLEURS language:
  en/ja/ru rows: ref_text = the clip's own transcript, ref_zh = the cmn_hans_cn transcript of the same FLoRes id,
                 converted to Taiwan Traditional with the app's zh_convert(..., "tw");
  zh rows:       ref_text = the clip's cmn_hans_cn transcript (Simplified, as FLEURS has it), ref_en = the en_us
                 transcript of the same FLoRes id.
Only test.tsv and the start of each audio/test.tar.gz are read: the archive is streamed and dropped as soon as the
pinned clips are found (they were pinned from the first members, see --pin), so the whole run reads a few MB.
--pin N re-selects N clips per language from the start of each archive at --revision and rewrites the pin file
(dev only; commit the result).
Downloads are untrusted data: only the pinned member names are read from the archive, nothing is extracted by path.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / "eval" / "ci_smoke.json"
DATASET = "google/fleurs"
BASE_URL = "https://huggingface.co/datasets/{dataset}/resolve/{revision}/data/{config}/{path}"
CONFIGS = {"en": "en_us", "ja": "ja_jp", "ru": "ru_ru", "zh": "cmn_hans_cn"}
MAX_DOWNLOAD = 500 * 2 ** 20  # #569 acceptance: total download < 500 MB
MAX_CLIP_S = 15.0  # --pin skips longer clips so 20 clips stay well inside the smoke budget (TOTAL_BUDGET_S 300 s)
SAMPLE_RATE = 16000

LICENSE_TEXT = """CI smoke set for tlwu-eval, built by scripts/bench/make_ci_smoke.py (#569) from the pins in
eval/ci_smoke.json.
Audio, transcripts (ref_text) and references (ref_zh / ref_en): FLEURS test split, Google, CC-BY 4.0
  https://huggingface.co/datasets/google/fleurs  (revision {revision})
ref_zh is the cmn_hans_cn transcript of the same FLoRes sentence converted to Taiwan Traditional script.
Each manifest row's "source" field names the FLEURS config and file it came from.
"""


class DownloadError(RuntimeError):
    pass


def http_open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "tlwu-bench/0.1"})
    return urllib.request.urlopen(req, timeout=120)


class _Counted(io.RawIOBase):
    """Read-only stream wrapper that counts bytes and stops the run once the shared budget is spent."""

    def __init__(self, raw, budget: dict):
        self.raw, self.budget = raw, budget

    def readable(self):
        return True

    def readinto(self, b):
        data = self.raw.read(len(b))
        n = len(data)
        b[:n] = data
        self.budget["used"] += n
        if self.budget["used"] > MAX_DOWNLOAD:
            raise DownloadError(f"download passed {MAX_DOWNLOAD >> 20} MB")
        return n


def url_for(revision: str, config: str, path: str) -> str:
    return BASE_URL.format(dataset=DATASET, revision=revision, config=config, path=path)


def read_tsv(data: bytes) -> dict:
    """FLEURS test.tsv -> {file_name: {"flores_id", "text", "samples"}} (raw_transcription keeps case/punctuation)."""
    rows = {}
    for line in data.decode("utf-8").splitlines():
        c = line.split("\t")
        if len(c) < 6:
            continue
        rows[c[1]] = {"flores_id": int(c[0]), "text": c[2].strip(), "samples": int(c[5])}
    return rows


def by_flores(rows: dict) -> dict:
    """{flores_id: transcript} using the first row (in file order) of each id: every reading has the same text."""
    out = {}
    for r in rows.values():
        out.setdefault(r["flores_id"], r["text"])
    return out


def fetch_bytes(fetch, url: str, budget: dict) -> bytes:
    with fetch(url) as r:
        return io.BufferedReader(_Counted(r, budget)).read()


def stream_members(fetch, url: str, budget: dict, want=None, limit: int = 0):
    """Yield (file_name, bytes) of .wav members of a streamed .tar.gz, in archive order. Stops reading the network
    as soon as every name in `want` was seen (or after `limit` wav members when want is None)."""
    want = set(want) if want is not None else None
    seen = 0
    with fetch(url) as r:
        with tarfile.open(fileobj=io.BufferedReader(_Counted(r, budget)), mode="r|gz") as t:
            for m in t:
                name = m.name.rsplit("/", 1)[-1]
                if not m.isfile() or not name.endswith(".wav"):
                    continue
                if want is None or name in want:
                    yield name, t.extractfile(m).read()
                    seen += 1
                    if want is not None:
                        want.discard(name)
                        if not want:
                            return
                    elif seen >= limit:
                        return


def check_wav(data: bytes) -> float:
    """Seconds of audio; raises ValueError unless a 16 kHz mono WAV. FLEURS ships 32-bit float WAVs (format 3), which
    the stdlib `wave` module refuses, so the RIFF chunks are read directly (tlwu_eval loads them with soundfile)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, fmt, size = 12, None, None
    while pos + 8 <= len(data):
        cid, n = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
        elif cid == b"data":
            size = min(n, len(data) - pos - 8)
            break
        pos += 8 + n + (n & 1)
    if fmt is None or size is None:
        raise ValueError("WAV without fmt/data chunk")
    _tag, channels, rate, _byte_rate, block, _bits = fmt
    if (rate, channels) != (SAMPLE_RATE, 1) or not block:
        raise ValueError(f"not 16 kHz mono: {rate} Hz, {channels} ch")
    return size / block / rate


def load_tsvs(fetch, revision: str, budget: dict) -> dict:
    return {lang: read_tsv(fetch_bytes(fetch, url_for(revision, cfg, "test.tsv"), budget))
            for lang, cfg in CONFIGS.items()}


def reference(lang: str, flores_id: int, refs: dict) -> tuple[str, str]:
    """(field, raw text) of the translation reference for a clip; KeyError when the other side lacks the id.
    ref_zh is still Simplified here: finish() converts it after every download, because importing tinglongwu turns
    on the app's offline network guard."""
    if lang == "zh":
        return "ref_en", refs["en"][flores_id]
    return "ref_zh", refs["zh"][flores_id]


def finish(out: Path, rows: list[dict], revision: str) -> None:
    """Convert ref_zh to Taiwan Traditional (the app's zh_convert "tw"); write manifest.jsonl + LICENSE.txt."""
    from tinglongwu.translate import zh_convert

    with open(out / "manifest.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            if "ref_zh" in r:
                r["ref_zh"] = zh_convert(r["ref_zh"], "tw")
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "LICENSE.txt").write_text(LICENSE_TEXT.format(revision=revision), encoding="utf-8")


def build(out, pin: dict, fetch=http_open) -> list[dict]:
    """Download the pinned clips into `out`, verify every sha256, write manifest.jsonl + LICENSE.txt; return the rows.
    Raises DownloadError (nothing but WAVs written) on a sha256 mismatch or a missing clip."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rev = pin["revision"]
    budget = {"used": 0}
    tsvs = load_tsvs(fetch, rev, budget)
    refs = {lang: by_flores(rows) for lang, rows in tsvs.items()}
    rows, bad = [], []
    for lang, cfg in CONFIGS.items():
        clips = [c for c in pin["clips"] if c["lang"] == lang]
        if not clips:
            continue
        files = {c["file"]: c for c in clips}
        got = dict(stream_members(fetch, url_for(rev, cfg, "audio/test.tar.gz"), budget, want=files))
        for c in clips:
            data = got.get(c["file"])
            if data is None:
                bad.append(f"{c['id']}: {cfg}/{c['file']} not in the archive")
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest != c["sha256"]:
                bad.append(f"{c['id']}: sha256 {digest} != pinned {c['sha256']}")
                continue
            (out / f"{c['id']}.wav").write_bytes(data)
            meta = tsvs[lang].get(c["file"])
            if meta is None:
                bad.append(f"{c['id']}: {cfg}/{c['file']} not in test.tsv")
                continue
            try:
                field, ref = reference(lang, meta["flores_id"], refs)
            except KeyError:
                bad.append(f"{c['id']}: no reference for FLoRes id {meta['flores_id']}")
                continue
            rows.append({"id": c["id"], "lang": lang, "wav": f"{c['id']}.wav", "ref_text": meta["text"], field: ref,
                         "style": "news", "source": f"fleurs/{cfg}/{c['file']}", "flores_id": meta["flores_id"]})
    if bad:
        raise DownloadError("pinned clips do not match:\n  " + "\n  ".join(bad))
    finish(out, rows, rev)
    print(f"{len(rows)} clips -> {out} ({budget['used'] / 2 ** 20:.1f} MB downloaded)")
    return rows


def pin_clips(revision: str, per_lang: int, fetch=http_open, scan: int = 60) -> dict:
    """Pick `per_lang` clips per language from the first `scan` members of each archive: distinct FLoRes ids that
    have a reference on the other side, at most MAX_CLIP_S long. Returns the pin dict (with sha256s)."""
    budget = {"used": 0}
    tsvs = load_tsvs(fetch, revision, budget)
    refs = {lang: by_flores(rows) for lang, rows in tsvs.items()}
    clips = []
    for lang, cfg in CONFIGS.items():
        picked, ids = [], set()
        for name, data in stream_members(fetch, url_for(revision, cfg, "audio/test.tar.gz"), budget, limit=scan):
            meta = tsvs[lang].get(name)
            if meta is None or meta["flores_id"] in ids:
                continue
            try:
                reference(lang, meta["flores_id"], refs)
                secs = check_wav(data)
            except (KeyError, ValueError):
                continue
            if secs > MAX_CLIP_S:
                continue
            ids.add(meta["flores_id"])
            picked.append({"id": f"{lang}_{len(picked) + 1:03d}", "lang": lang, "file": name,
                           "flores_id": meta["flores_id"], "seconds": round(secs, 2),
                           "sha256": hashlib.sha256(data).hexdigest()})
            if len(picked) == per_lang:
                break
        if len(picked) < per_lang:
            raise DownloadError(f"{cfg}: only {len(picked)} usable clips in the first {scan} members")
        clips += picked
    return {"_note": "Pins for scripts/bench/make_ci_smoke.py (#569): the open-licensed CI smoke set (FLEURS test, "
                     "CC-BY 4.0). Re-pin with --pin 5 only on purpose; eval/baseline.json numbers depend on these clips.",
            "dataset": DATASET, "revision": revision, "license": "CC-BY-4.0", "clips": clips}


def main(argv=None, fetch=http_open) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="folder for manifest.jsonl and the wav files (e.g. eval_data/smoke)")
    ap.add_argument("--pins", default=str(PIN_FILE), help="pin file (default eval/ci_smoke.json)")
    ap.add_argument("--pin", type=int, metavar="N", help="dev: re-select N clips per language and rewrite --pins")
    ap.add_argument("--revision", help="with --pin: FLEURS dataset revision (commit sha) to pin")
    a = ap.parse_args(argv)
    try:
        if a.pin:
            if not a.revision:
                ap.error("--pin needs --revision")
            pin = pin_clips(a.revision, a.pin, fetch)
            Path(a.pins).write_text(json.dumps(pin, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"pinned {len(pin['clips'])} clips -> {a.pins}")
            return 0
        if not a.out:
            ap.error("--out is required")
        build(a.out, json.loads(Path(a.pins).read_text("utf-8")), fetch)
    except DownloadError as e:
        print(f"make_ci_smoke: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))  # tinglongwu.translate.zh_convert when run from a checkout without `pip install -e`
    raise SystemExit(main())
