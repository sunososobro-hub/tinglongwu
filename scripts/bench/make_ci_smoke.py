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

Tier-2 set (#602): the same tool builds the tier-2 language set (vi/th/id/es/de/fr -> zh, 20 clips each) from
eval/tier2_pins.json. The pin file carries its own "configs" map, so --out alone rebuilds it:
  python scripts/bench/make_ci_smoke.py --pins eval/tier2_pins.json --out eval_data/tier2
  python -m tlwu_eval --smoke --data eval_data/tier2 --langs vi,th,id,es,de,fr --per-lang 20 --no-noise --out t2.json
  python scripts/bench/make_ci_smoke.py --set tier2 --pin 20 --revision <sha>    # dev: re-pin the tier-2 set

Korean slice (#754): ko -> zh-TW, 5 clips, same flow with its own pin file:
  python scripts/bench/make_ci_smoke.py --set ko --pin 5 --revision <sha>        # dev: pin eval/ko_pins.json
  python scripts/bench/make_ci_smoke.py --pins eval/ko_pins.json --out eval_data/ko
  python -m tlwu_eval --smoke --data eval_data/ko --langs ko --clips 5 --no-noise --out ko.json   # prints chrF
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
# #596/#602: tier-2 languages (docs/support.json), each -> zh. FLEURS has Latin-American Spanish only (es_419).
TIER2_CONFIGS = {"vi": "vi_vn", "th": "th_th", "id": "id_id", "es": "es_419", "de": "de_de", "fr": "fr_fr"}
KO_CONFIGS = {"ko": "ko_kr"}  # #754: Korean language pack -> its own ko->zh-TW slice (eval/ko_pins.json)
REF_CONFIGS = {"en": "en_us", "zh": "cmn_hans_cn"}  # reference sides: ref_zh for every source, ref_en for zh clips
MAX_DOWNLOAD = 500 * 2 ** 20  # #569 acceptance: total download < 500 MB
MAX_CLIP_S = 15.0  # --pin skips longer clips so 20 clips stay well inside the smoke budget (TOTAL_BUDGET_S 300 s)
SAMPLE_RATE = 16000
# --set: which languages --pin selects, the default pin file, and how many archive members --pin scans per language.
SETS = {
    "smoke": {"configs": CONFIGS, "pins": PIN_FILE, "scan": 60,
              "note": "Pins for scripts/bench/make_ci_smoke.py (#569): the open-licensed CI smoke set (FLEURS test, "
                      "CC-BY 4.0). Re-pin with --pin 5 only on purpose; eval/baseline.json numbers depend on these clips."},
    "tier2": {"configs": TIER2_CONFIGS, "pins": ROOT / "eval" / "tier2_pins.json", "scan": 120,
              "note": "Pins for scripts/bench/make_ci_smoke.py --pins eval/tier2_pins.json (#602): tier-2 languages "
                      "-> zh (FLEURS test, CC-BY 4.0). Re-pin with --set tier2 --pin 20 only on purpose; "
                      "eval/tier2_report.json numbers depend on these clips."},
    "ko": {"configs": KO_CONFIGS, "pins": ROOT / "eval" / "ko_pins.json", "scan": 60,
           "note": "Pins for scripts/bench/make_ci_smoke.py --pins eval/ko_pins.json (#754): Korean -> zh-TW slice "
                   "(FLEURS ko_kr test, CC-BY 4.0). Re-pin with --set ko --pin 5 only on purpose; the recorded ko->zh "
                   "chrF baseline depends on these clips."},
}

LICENSE_TEXT = """Eval set for tlwu-eval, built by scripts/bench/make_ci_smoke.py (#569, #602) from the pins in
{pins}.
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


def ref_configs(configs: dict) -> dict:
    """The source configs plus the reference sides they need: zh for every non-zh source, en for zh clips."""
    need = dict(configs)
    if any(lang != "zh" for lang in configs):
        need.setdefault("zh", REF_CONFIGS["zh"])
    if "zh" in configs:
        need.setdefault("en", REF_CONFIGS["en"])
    return need


def load_tsvs(fetch, revision: str, budget: dict, configs: dict = CONFIGS) -> dict:
    return {lang: read_tsv(fetch_bytes(fetch, url_for(revision, cfg, "test.tsv"), budget))
            for lang, cfg in ref_configs(configs).items()}


def reference(lang: str, flores_id: int, refs: dict) -> tuple[str, str]:
    """(field, raw text) of the translation reference for a clip; KeyError when the other side lacks the id.
    ref_zh is still Simplified here: finish() converts it after every download, because importing tinglongwu turns
    on the app's offline network guard."""
    if lang == "zh":
        return "ref_en", refs["en"][flores_id]
    return "ref_zh", refs["zh"][flores_id]


def finish(out: Path, rows: list[dict], revision: str, pins: str = "eval/ci_smoke.json") -> None:
    """Convert ref_zh to Taiwan Traditional (the app's zh_convert "tw"); write manifest.jsonl + LICENSE.txt."""
    from tinglongwu.translate import zh_convert

    with open(out / "manifest.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            if "ref_zh" in r:
                r["ref_zh"] = zh_convert(r["ref_zh"], "tw")
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "LICENSE.txt").write_text(LICENSE_TEXT.format(revision=revision, pins=pins), encoding="utf-8")


def build(out, pin: dict, fetch=http_open, pins_name: str = "eval/ci_smoke.json") -> list[dict]:
    """Download the pinned clips into `out`, verify every sha256, write manifest.jsonl + LICENSE.txt; return the rows.
    The pin's optional "configs" map ({lang: FLEURS config}) names the source languages (default: the smoke CONFIGS).
    Raises DownloadError (nothing but WAVs written) on a sha256 mismatch or a missing clip."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rev = pin["revision"]
    configs = pin.get("configs") or CONFIGS
    budget = {"used": 0}
    tsvs = load_tsvs(fetch, rev, budget, configs)
    refs = {lang: by_flores(rows) for lang, rows in tsvs.items()}
    rows, bad = [], []
    unknown = sorted({c["lang"] for c in pin["clips"]} - set(configs))
    if unknown:
        raise DownloadError(f"pinned clips in languages without a FLEURS config: {', '.join(unknown)}")
    for lang, cfg in configs.items():
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
    finish(out, rows, rev, pins_name)
    print(f"{len(rows)} clips -> {out} ({budget['used'] / 2 ** 20:.1f} MB downloaded)")
    return rows


def pin_clips(revision: str, per_lang: int, fetch=http_open, scan: int = 60, set_name: str = "smoke") -> dict:
    """Pick `per_lang` clips per language of SETS[set_name] from the first `scan` members of each archive: distinct
    FLoRes ids that have a reference on the other side, at most MAX_CLIP_S long. Returns the pin dict (with sha256s);
    a set other than the smoke set also records its "configs" map, so build() needs only the pin file."""
    spec = SETS[set_name]
    configs = spec["configs"]
    budget = {"used": 0}
    tsvs = load_tsvs(fetch, revision, budget, configs)
    refs = {lang: by_flores(rows) for lang, rows in tsvs.items()}
    clips = []
    for lang, cfg in configs.items():
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
    pin = {"_note": spec["note"], "dataset": DATASET, "revision": revision, "license": "CC-BY-4.0"}
    if configs is not CONFIGS:
        pin["configs"] = dict(configs)
    pin["clips"] = clips
    return pin


def dump_pin(pin: dict) -> str:
    """JSON with one clip per line (short, reviewable diffs when re-pinned)."""
    head = {k: v for k, v in pin.items() if k != "clips"}
    lines = [json.dumps(head, ensure_ascii=False)[:-1] + ', "clips": [']
    lines += ["  " + json.dumps(c, ensure_ascii=False) + "," for c in pin["clips"]]
    lines[-1] = lines[-1].rstrip(",")
    return "\n".join(lines) + "\n]}\n"


def main(argv=None, fetch=http_open) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="folder for manifest.jsonl and the wav files (e.g. eval_data/smoke)")
    ap.add_argument("--set", default="smoke", choices=sorted(SETS),
                    help="language set for --pin and the default --pins (smoke: en/ja/ru/zh; tier2: vi/th/id/es/de/fr; "
                         "ko: ko)")
    ap.add_argument("--pins", default=None, help="pin file (default: eval/ci_smoke.json, eval/tier2_pins.json "
                                                 "with --set tier2, eval/ko_pins.json with --set ko)")
    ap.add_argument("--pin", type=int, metavar="N", help="dev: re-select N clips per language and rewrite --pins")
    ap.add_argument("--revision", help="with --pin: FLEURS dataset revision (commit sha) to pin")
    a = ap.parse_args(argv)
    a.pins = a.pins or str(SETS[a.set]["pins"])
    try:
        if a.pin:
            if not a.revision:
                ap.error("--pin needs --revision")
            pin = pin_clips(a.revision, a.pin, fetch, SETS[a.set]["scan"], a.set)
            Path(a.pins).write_text(dump_pin(pin), encoding="utf-8")
            print(f"pinned {len(pin['clips'])} clips -> {a.pins}")
            return 0
        if not a.out:
            ap.error("--out is required")
        if not Path(a.pins).is_file():
            print(f"make_ci_smoke: pin file not found: {a.pins} (run --set {a.set} --pin N to create it)",
                  file=sys.stderr)
            return 1
        pins_name = Path(a.pins).resolve()
        pins_name = pins_name.relative_to(ROOT).as_posix() if pins_name.is_relative_to(ROOT) else pins_name.name
        build(a.out, json.loads(Path(a.pins).read_text("utf-8")), fetch, pins_name)
    except DownloadError as e:
        print(f"make_ci_smoke: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))  # tinglongwu.translate.zh_convert when run from a checkout without `pip install -e`
    raise SystemExit(main())
