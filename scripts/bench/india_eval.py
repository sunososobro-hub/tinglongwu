#!/usr/bin/env python3
"""#620: Indian speakers -> Chinese subtitles. Indian-accented English or Hindi: which gives better Chinese?

Dev tool (not part of the offline runtime). Runs on cloud CPU runners (`.github/workflows/eval-india.yml`):

  python scripts/bench/india_eval.py fetch --group en|hi|svarah --data DATA [--n 50]    # network
  TLWU_HYMT_TEMP=0 python scripts/bench/india_eval.py run --group en|hi|svarah --data DATA --out OUT \
      --models-dir MODELS [--whisper large-v3-turbo]                                       # offline, CPU
  python scripts/bench/india_eval.py report OUT                                            # needs sacrebleu

Groups (same ASR = Whisper, same MT = Hy-MT2-1.8B Q4_K_M on the CPU, as the app ships):
  en      FLEURS en_us test (US-accented read English), the first N FLoRes sentences that FLEURS also has in hi_in.
  hi      FLEURS hi_in test, the SAME N FLoRes sentences (FLEURS reads the same sentences in every language), so en
          vs hi is like for like. Translated two ways from the ASR text: direct hi->zh, and pivot hi->en->zh.
  svarah  Svarah test (AI4Bharat, Indian-accented English from many states; licence per its dataset card), N clips spread evenly over
          the set. Svarah has no Chinese reference, so its translation score is chrF against Hy-MT2's translation of
          the human transcript ("vs gold-transcript MT": how much the ASR errors cost in Chinese). The same score is
          given for en and hi, so all three groups compare on it.
FLEURS groups also get chrF against the human Chinese reference: the cmn_hans_cn transcript of the same FLoRes id,
converted to Taiwan Traditional with the app's zh_convert (same convention as make_ci_smoke.py / eval/tier2_report).
Downloads are untrusted data: archive members are matched by base name and written under our own names only.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FLEURS = "google/fleurs"
FLEURS_REV = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"  # same pin as eval/tier2_pins.json
CONFIGS = {"en": "en_us", "hi": "hi_in", "zh": "cmn_hans_cn"}
SVARAH = "ai4bharat/Svarah"
SAMPLE_RATE = 16000
MAX_CLIP_S = 20.0
GROUPS = ("en", "hi", "svarah")


# ---------- selection (pure) ----------

def read_tsv(text: str) -> list[dict]:
    """FLEURS test.tsv -> rows in file order: flores_id (col 0, shared by every language), file, raw text, seconds."""
    rows = []
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) < 6 or not c[0].isdigit():
            continue
        rows.append({"flores_id": int(c[0]), "file": c[1], "text": c[2], "seconds": int(c[5]) / SAMPLE_RATE})
    return rows


def select_fleurs(tsvs: dict, n: int, max_s: float = MAX_CLIP_S) -> list[dict]:
    """First `n` distinct FLoRes ids (en_us file order) that have an en clip and a hi clip of at most `max_s` and a
    zh transcript. Each row: flores_id, en/hi {file, text}, ref_zh (Simplified, converted at write time)."""
    first = {}
    for lang in ("hi", "zh"):
        first[lang] = {}
        for r in tsvs[lang]:
            if lang == "zh" or r["seconds"] <= max_s:
                first[lang].setdefault(r["flores_id"], r)
    out, seen = [], set()
    for r in tsvs["en"]:
        fid = r["flores_id"]
        if fid in seen or r["seconds"] > max_s or fid not in first["hi"] or fid not in first["zh"]:
            continue
        seen.add(fid)
        h = first["hi"][fid]
        out.append({"flores_id": fid, "en": {"file": r["file"], "text": r["text"]},
                    "hi": {"file": h["file"], "text": h["text"]}, "ref_zh": first["zh"][fid]["text"]})
        if len(out) == n:
            break
    return out


def spread(total: int, n: int) -> list[int]:
    """`n` indices spread evenly over range(total) (Svarah is ordered by speaker: this covers many speakers)."""
    if total <= n:
        return list(range(total))
    return [int(i * total / n) for i in range(n)]


def extract_members(tar_bytes_or_file, want: dict) -> dict:
    """Stream a tar(.gz) and return {base name: bytes} for the base names in `want` only; never extracts by path."""
    got = {}
    with tarfile.open(fileobj=tar_bytes_or_file, mode="r|*") as tf:
        for m in tf:
            if not m.isfile():
                continue
            name = m.name.rsplit("/", 1)[-1]
            if name in want and name not in got:
                got[name] = tf.extractfile(m).read()
                if len(got) == len(want):
                    break
    return got


# ---------- fetch (network; must not import tinglongwu: its network guard would block the downloads) ----------

def fetch_fleurs(data: Path, group: str, n: int) -> list[dict]:
    from huggingface_hub import hf_hub_download

    def get(cfg, path):
        return Path(hf_hub_download(FLEURS, f"data/{cfg}/{path}", repo_type="dataset", revision=FLEURS_REV))

    tsvs = {lang: read_tsv(get(cfg, "test.tsv").read_text("utf-8")) for lang, cfg in CONFIGS.items()}
    rows = select_fleurs(tsvs, n)
    want = {r[group]["file"]: r for r in rows}
    with open(get(CONFIGS[group], "audio/test.tar.gz"), "rb") as f:
        got = extract_members(f, want)
    out = data / group
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, r in enumerate(rows):
        b = got.get(r[group]["file"])
        if b is None:
            raise SystemExit(f"india_eval: {CONFIGS[group]}/{r[group]['file']} not in the archive")
        wav = f"{group}_{i + 1:03d}.wav"
        (out / wav).write_bytes(b)
        manifest.append({"id": wav[:-4], "wav": wav, "lang": group, "flores_id": r["flores_id"],
                         "ref_text": r[group]["text"], "ref_zh_hans": r["ref_zh"],
                         "source": f"fleurs/{CONFIGS[group]}/{r[group]['file']}@{FLEURS_REV[:12]}"})
    return manifest


def fetch_svarah(data: Path, n: int) -> list[dict]:
    """Parquet shards of the Svarah test split. Raises RuntimeError with the reason when the set is unreachable
    (e.g. gated) or its columns are not what we expect."""
    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    info = api.dataset_info(SVARAH)
    files = sorted(f for f in api.list_repo_files(SVARAH, repo_type="dataset", revision=info.sha)
                   if f.endswith(".parquet"))
    if not files:
        raise RuntimeError(f"{SVARAH}@{info.sha}: no parquet files ({len(files)})")
    tables = [pq.read_table(hf_hub_download(SVARAH, f, repo_type="dataset", revision=info.sha)) for f in files]
    cols = tables[0].column_names
    audio_col = next((c for c in cols if c.startswith("audio")), None)
    text_col = next((c for c in ("text", "transcript", "transcription", "sentence") if c in cols), None)
    if not audio_col or not text_col:
        raise RuntimeError(f"{SVARAH}: unexpected columns {cols}")
    import pyarrow as pa

    table = pa.concat_tables(tables)
    idx = spread(table.num_rows, n)
    rows = table.take(idx).to_pylist()  # only the picked rows become Python objects (the audio is ~10 h)
    out = data / "svarah"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for k, (i, r) in enumerate(zip(idx, rows)):
        a = r[audio_col]
        b = a.get("bytes") if isinstance(a, dict) else None
        if not b:
            raise RuntimeError(f"{SVARAH}: row {i} has no audio bytes")
        ext = Path((a.get("path") or "x.wav")).suffix.lower()
        name = f"svarah_{k + 1:03d}{ext if ext in ('.wav', '.flac', '.mp3', '.ogg') else '.wav'}"
        (out / name).write_bytes(b)
        meta = {c: r[c] for c in cols if c not in (audio_col, text_col) and isinstance(r[c], (str, int, float))}
        manifest.append({"id": name.rsplit(".", 1)[0], "wav": name, "lang": "en", "ref_text": r[text_col],
                         "source": f"svarah/{info.sha[:12]}/row{i}", "meta": meta})
    return manifest


def cmd_fetch(a) -> int:
    data = Path(a.data)
    (data / a.group).mkdir(parents=True, exist_ok=True)
    status = data / a.group / "status.json"
    try:
        rows = fetch_svarah(data, a.n) if a.group == "svarah" else fetch_fleurs(data, a.group, a.n)
    except Exception as e:  # noqa: BLE001 - recorded for the report, the job goes on
        if a.group != "svarah":
            raise
        status.write_text(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}), "utf-8")
        print(f"india_eval: Svarah unavailable: {e}", file=sys.stderr)
        return 0
    with open(data / a.group / "manifest.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    status.write_text(json.dumps({"ok": True, "clips": len(rows)}), "utf-8")
    print(f"india_eval: {len(rows)} {a.group} clips -> {data / a.group}")
    return 0


# ---------- run (offline: tinglongwu ASR + MT on the CPU) ----------

def mt_plan(group: str) -> dict:
    """Translation routes per group: name -> (text field, list of (src, tgt) hops)."""
    if group == "hi":
        return {"zh_direct": ("asr", [("hi", "zh")]), "zh_pivot": ("asr", [("hi", "en"), ("en", "zh")]),
                "zh_gold": ("ref_text", [("hi", "zh")])}
    return {"zh_direct": ("asr", [("en", "zh")]), "zh_gold": ("ref_text", [("en", "zh")])}


def run_group(rows: list[dict], group: str, asr, tr, load_audio, clock=time.perf_counter) -> list[dict]:
    """ASR each clip, then every translation route of mt_plan(group); per-clip timings in seconds."""
    out = []
    for r in rows:
        x = load_audio(r)
        t0 = clock()
        t = asr.transcribe(x)
        d = dict(r, asr=(t.text if t else "").strip(), asr_s=round(clock() - t0, 3),
                 audio_s=round(len(x) / SAMPLE_RATE, 3))
        for name, (field, hops) in mt_plan(group).items():
            text, t0 = d[field], clock()
            for src, tgt in hops:
                text = tr.translate(text, src, tgt).text.strip() if text else ""
            d[name], d[name + "_s"] = text, round(clock() - t0, 3)
        out.append(d)
    return out


def load_audio(path: Path):
    """Any soundfile-readable clip -> float32 mono 16 kHz (faster_whisper.decode_audio breaks on newer PyAV)."""
    from math import gcd

    import numpy as np
    import soundfile as sf

    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SAMPLE_RATE:
        from scipy.signal import resample_poly

        g = gcd(sr, SAMPLE_RATE)
        x = resample_poly(x, SAMPLE_RATE // g, sr // g).astype(np.float32)
    return x


def cmd_run(a) -> int:
    sys.path.insert(0, str(REPO))
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.hymt import HyMtTranslator
    from tinglongwu.translate import zh_convert

    data, out = Path(a.data) / a.group, Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    status = json.loads((data / "status.json").read_text("utf-8"))
    (out / f"{a.group}.status.json").write_text(json.dumps(status), "utf-8")
    if not status.get("ok"):
        return 0
    rows = [json.loads(x) for x in (data / "manifest.jsonl").read_text("utf-8").splitlines() if x.strip()]
    for r in rows:
        if "ref_zh_hans" in r:
            r["ref_zh"] = zh_convert(r.pop("ref_zh_hans"), "tw")
    md = Path(a.models_dir)
    asr = FasterWhisperRecognizer(md, a.whisper, "hi" if a.group == "hi" else "en", "cpu")
    tr = HyMtTranslator(md, "tw", "cpu")
    tr.preload()
    res = run_group(rows, a.group, asr, tr, lambda r: load_audio(data / r["wav"]))
    with open(out / f"{a.group}.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for d in res:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    (out / f"{a.group}.run.json").write_text(json.dumps({"whisper": a.whisper, "mt": "Hy-MT2-1.8B Q4_K_M CPU",
                                                         "cpu": os.cpu_count()}), "utf-8")
    print(f"india_eval: {len(res)} {a.group} clips -> {out}")
    return 0


# ---------- report ----------

def asr_error_rate(rows: list[dict], lang: str) -> float:
    """Corpus WER: total word edits / total reference words (scripts/wer.py tokenizer, punctuation dropped)."""
    sys.path.insert(0, str(REPO / "scripts"))
    import wer

    errs = units = 0
    for r in rows:
        ref, hyp = wer.tokenize(r["ref_text"], lang), wer.tokenize(r["asr"], lang)
        errs += wer.edit_distance(ref, hyp)
        units += len(ref)
    return round(errs / units, 4) if units else None


def chrf(hyps: list[str], refs: list[str]) -> float:
    from sacrebleu.metrics import CHRF

    return round(CHRF().corpus_score(hyps, [refs]).score, 2)


def med(xs):
    return round(statistics.median(xs), 3) if xs else None


def summarize(group: str, rows: list[dict]) -> list[dict]:
    """One table row per translation route of the group (gold-transcript route excluded: it is the pseudo-ref)."""
    lang = "hi" if group == "hi" else "en"
    base = {"group": group, "clips": len(rows), "asr_wer": asr_error_rate(rows, lang),
            "asr_median_s": med([r["asr_s"] for r in rows]),
            "asr_rtf": round(sum(r["asr_s"] for r in rows) / max(sum(r["audio_s"] for r in rows), 1e-9), 3)}
    gold = [r["zh_gold"] for r in rows]
    out = []
    for route in [k for k in mt_plan(group) if k != "zh_gold"]:
        hyps = [r[route] for r in rows]
        row = dict(base, route=route, chrf_vs_gold_mt=chrf(hyps, gold), mt_median_s=med([r[route + "_s"] for r in rows]))
        row["chrf_vs_human_zh"] = chrf(hyps, [r["ref_zh"] for r in rows]) if all("ref_zh" in r for r in rows) else None
        row["chrf_gold_text_vs_human_zh"] = (chrf(gold, [r["ref_zh"] for r in rows])
                                             if all("ref_zh" in r for r in rows) else None)
        out.append(row)
    return out


LABEL = {("en", "zh_direct"): "FLEURS en_us (US English) -> zh",
         ("hi", "zh_direct"): "FLEURS hi_in (Hindi) -> zh, direct",
         ("hi", "zh_pivot"): "FLEURS hi_in (Hindi) -> en -> zh, pivot",
         ("svarah", "zh_direct"): "Svarah (Indian English) -> zh"}


def table(rows: list[dict], missing: dict) -> str:
    lines = ["| group | clips | ASR WER | chrF vs human zh | chrF vs gold-transcript MT | chrF from gold transcript "
             "| ASR median s (RTF) | MT median s |", "|---|---|---|---|---|---|---|---|"]

    def f(v, pct=False):
        return "-" if v is None else (f"{100 * v:.1f}%" if pct else f"{v}")

    for r in rows:
        lines.append(f"| {LABEL.get((r['group'], r['route']), r['group'] + ' ' + r['route'])} | {r['clips']} "
                     f"| {f(r['asr_wer'], True)} | {f(r['chrf_vs_human_zh'])} | {f(r['chrf_vs_gold_mt'])} "
                     f"| {f(r['chrf_gold_text_vs_human_zh'])} | {r['asr_median_s']} ({r['asr_rtf']}) "
                     f"| {r['mt_median_s']} |")
    for g, why in missing.items():
        lines.append(f"| {g}: not measured ({why}) | 0 | - | - | - | - | - | - |")
    return "\n".join(lines)


def cmd_report(a) -> int:
    d = Path(a.out)
    rows, missing = [], {}
    for g in GROUPS:
        st = d / f"{g}.status.json"
        if not st.exists():
            missing[g] = "no result"
            continue
        s = json.loads(st.read_text("utf-8"))
        if not s.get("ok"):
            missing[g] = s.get("error", "unavailable")[:160].replace("|", "/")
            continue
        rows += summarize(g, [json.loads(x) for x in (d / f"{g}.jsonl").read_text("utf-8").splitlines() if x.strip()])
    (d / "summary.json").write_text(json.dumps({"rows": rows, "missing": missing}, indent=1, ensure_ascii=False),
                                    "utf-8")
    print(table(rows, missing))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--group", required=True, choices=GROUPS)
    f.add_argument("--data", required=True)
    f.add_argument("--n", type=int, default=50)
    r = sub.add_parser("run")
    r.add_argument("--group", required=True, choices=GROUPS)
    r.add_argument("--data", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS", ""))
    r.add_argument("--whisper", default="large-v3-turbo")
    p = sub.add_parser("report")
    p.add_argument("out")
    a = ap.parse_args(argv)
    return {"fetch": cmd_fetch, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
