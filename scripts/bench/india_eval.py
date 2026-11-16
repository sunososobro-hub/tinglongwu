#!/usr/bin/env python3
"""#620: Indian speakers -> Chinese subtitles. Indian-accented English or Hindi: which gives better Chinese?

Dev tool (not part of the offline runtime). Runs on cloud CPU runners (`.github/workflows/eval-india.yml`):

  python scripts/bench/india_eval.py fetch --group en|hi|svarah|hinglish --data DATA [--n 50]    # network
  TLWU_HYMT_TEMP=0 python scripts/bench/india_eval.py run --group en|hi|svarah|hinglish --data DATA --out OUT \
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
  hinglish  #655: up to 10 Hindi-English code-switched clips from an open (ungated, permissive licence) speech set on
          the Hugging Face Hub, found by search at fetch time (or named with HINGLISH_DATASET=repo[@revision]); the
          dataset id, revision and licence are recorded per clip and in the report. ASR runs as Hindi (the language a
          Hinglish speaker picks) and the text is translated like hi (direct and pivot). Only "vs gold-transcript MT"
          applies (no Chinese reference); its ASR WER also counts script differences (Devanagari vs Roman Hindi).
FLEURS groups also get chrF against the human Chinese reference: the cmn_hans_cn transcript of the same FLoRes id,
converted to Taiwan Traditional with the app's zh_convert (same convention as make_ci_smoke.py / eval/tier2_report).
Downloads are untrusted data: archive members are matched by base name and written under our own names only.
"""
from __future__ import annotations

import argparse
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
# #655: Svarah is gated on Hugging Face. eval-india passes the HF_TOKEN repo secret (a read token of an account that
# accepted the Svarah terms) to the svarah fetch only; without it the group is reported as missing with this hint.
GATED_HINT = "set the HF_TOKEN repo secret to a read token of an account that accepted the Svarah terms (#655)"
SAMPLE_RATE = 16000
MAX_CLIP_S = 20.0
# #655: Hinglish. No pinned set: fetch searches the Hub for these words and keeps only ungated audio sets under one
# of OPEN_LICENSES that are tagged Hinglish (or both Hindi and English); see hinglish_candidates().
HINGLISH_N = 10
HINGLISH_SEARCH = ("hinglish", "code-switch", "code-switched", "codeswitch", "code-mixed", "code-switching")
OPEN_LICENSES = frozenset({"cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0", "cc-by-3.0", "cc-by-sa-3.0", "cc-by-2.0",
                           "apache-2.0", "mit", "odc-by", "pddl", "cdla-permissive-2.0", "bsd-3-clause"})
HINGLISH_MAX_TRIES = 8
CODE_SWITCH = ("code-switch", "codeswitch", "code_switch", "code-mix", "codemix", "code_mix")
GROUPS = ("en", "hi", "svarah", "hinglish")
OPTIONAL = ("svarah", "hinglish")  # an unreachable optional set is reported as missing; FLEURS failing fails the job
HINDI_LIKE = ("hi", "hinglish")  # ASR language hi, translated hi->zh direct and hi->en->zh pivot


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


def pick_columns(cols: list[str]):
    """(audio column, transcript column) of a Hub speech table, or None for either when missing."""
    audio = "audio" if "audio" in cols else next((c for c in cols if c.startswith("audio")), None)
    text = next((c for c in ("text", "transcript", "transcription", "sentence", "normalized_text", "transcripts")
                 if c in cols), None)
    return audio, text


def _tags(info) -> list[str]:
    return [str(t).lower() for t in (getattr(info, "tags", None) or [])]


def hinglish_candidates(infos) -> list[str]:
    """Hub dataset infos -> ids worth trying for the hinglish group, most downloaded first (ties by id).
    Kept: not gated; a licence tag in OPEN_LICENSES; audio (modality:audio or an ASR task tag); and code-switch
    evidence: the word hinglish in the id or tags, or a code-switch word (CODE_SWITCH) in the id or tags plus both
    language:hi and language:en tags (a hi-ta code-switch set is not Hinglish). A set tagged only hi + en is not enough: it may hold separate Hindi-only and English-only clips."""
    keep = {}
    for d in infos:
        rid = getattr(d, "id", None)
        if not rid or rid in keep or getattr(d, "gated", False) not in (False, None):
            continue
        tags = _tags(d)
        if not any(t.startswith("license:") and t.split(":", 1)[1] in OPEN_LICENSES for t in tags):
            continue
        if "modality:audio" not in tags and not any("automatic-speech-recognition" in t for t in tags):
            continue
        text = " ".join([rid.lower(), *tags])
        if "hinglish" not in text and not (any(w in text for w in CODE_SWITCH) and {"language:hi", "language:en"} <= set(tags)):
            continue
        keep[rid] = getattr(d, "downloads", 0) or 0
    return sorted(keep, key=lambda r: (-keep[r], r))


def open_licence(tags: list[str]) -> str:
    """The first licence tag in OPEN_LICENSES (the rule hinglish_candidates() uses), else the first licence tag,
    else "unknown"."""
    lics = [t.split(":", 1)[1] for t in tags if t.startswith("license:")]
    return next((x for x in lics if x in OPEN_LICENSES), lics[0] if lics else "unknown")


def clip_seconds(b: bytes):
    """Duration of an encoded clip in seconds, or None when soundfile cannot read it."""
    import io

    try:
        import soundfile as sf

        i = sf.info(io.BytesIO(b))
        return i.frames / i.samplerate if i.samplerate else None
    except Exception:  # noqa: BLE001 - undecodable rows are skipped
        return None


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

    token = os.environ.get("HF_TOKEN") or None  # #655: gated set
    api = HfApi(token=token)
    info = api.dataset_info(SVARAH)
    files = sorted(f for f in api.list_repo_files(SVARAH, repo_type="dataset", revision=info.sha)
                   if f.endswith(".parquet"))
    if not files:
        raise RuntimeError(f"{SVARAH}@{info.sha}: no parquet files ({len(files)})")
    tables = [pq.read_table(hf_hub_download(SVARAH, f, repo_type="dataset", revision=info.sha, token=token)) for f in files]
    cols = tables[0].column_names
    audio_col, text_col = pick_columns(cols)
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
        ext = Path(a.get("path") or "x.wav").suffix.lower()
        name = f"svarah_{k + 1:03d}{ext if ext in ('.wav', '.flac', '.mp3', '.ogg') else '.wav'}"
        (out / name).write_bytes(b)
        meta = {c: r[c] for c in cols if c not in (audio_col, text_col) and isinstance(r[c], (str, int, float))}
        manifest.append({"id": name.rsplit(".", 1)[0], "wav": name, "lang": "en", "ref_text": r[text_col],
                         "source": f"svarah/{info.sha[:12]}/row{i}", "meta": meta})
    return manifest


def fetch_hinglish_from(repo: str, revision, data: Path, n: int, api, download, read_table) -> list[dict]:
    """Up to `n` clips of 1..MAX_CLIP_S seconds with a transcript from the parquet shards of one Hub set (test split
    shards when there are any; at most 2 shards, so a big set costs one or two downloads). Rows are visited spread
    over the shards' rows. Raises RuntimeError when the set has no usable parquet audio + text."""
    info = api.dataset_info(repo, revision=revision)
    sha = info.sha
    lic = open_licence(_tags(info))
    # the same rule as hinglish_candidates(), also for a HINGLISH_DATASET override; checked before any download
    if getattr(info, "gated", False) not in (False, None):
        raise RuntimeError(f"{repo}: gated set, not an open Hinglish set")
    if lic not in OPEN_LICENSES:
        raise RuntimeError(f"{repo}: licence {lic!r} is not in OPEN_LICENSES")
    files = sorted(f for f in api.list_repo_files(repo, repo_type="dataset", revision=sha) if f.endswith(".parquet"))
    test = [f for f in files if "test" in f.rsplit("/", 1)[-1].lower() or "/test" in f.lower()]
    files = (test or files)[:2]
    if not files:
        raise RuntimeError(f"{repo}@{sha[:12]}: no parquet files")
    import pyarrow as pa  # after the checks: a rejected set needs no pyarrow (the ci test job has none)

    table = pa.concat_tables([read_table(download(repo, f, repo_type="dataset", revision=sha)) for f in files],
                             promote_options="default")
    audio_col, text_col = pick_columns(table.column_names)
    if not audio_col or not text_col:
        raise RuntimeError(f"{repo}: unexpected columns {table.column_names}")
    out = data / "hinglish"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i in spread(table.num_rows, max(n * 5, n)):
        r = table.slice(i, 1).to_pylist()[0]
        a, text = r[audio_col], (r[text_col] or "")
        b = a.get("bytes") if isinstance(a, dict) else None
        if not b or not isinstance(text, str) or not text.strip():
            continue
        s = clip_seconds(b)
        if s is None or not 1.0 <= s <= MAX_CLIP_S:
            continue
        ext = Path(a.get("path") or "x.wav").suffix.lower()
        name = f"hinglish_{len(manifest) + 1:03d}{ext if ext in ('.wav', '.flac', '.mp3', '.ogg') else '.wav'}"
        (out / name).write_bytes(b)
        manifest.append({"id": name.rsplit(".", 1)[0], "wav": name, "lang": "hi", "ref_text": text.strip(),
                         "source": f"{repo}@{sha[:12]}/row{i}", "license": lic})
        if len(manifest) == n:
            break
    if not manifest:
        raise RuntimeError(f"{repo}@{sha[:12]}: no clip of 1-{MAX_CLIP_S:g} s with a transcript")
    return manifest


def fetch_hinglish(data: Path, n: int) -> list[dict]:
    """#655: the first candidate set (HINGLISH_DATASET, else hinglish_candidates() of a Hub search) that yields clips.
    Raises RuntimeError listing what was tried when none does."""
    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    override = os.environ.get("HINGLISH_DATASET", "").strip()
    if override:
        repo, _, rev = override.partition("@")
        cands = [(repo, rev or None)]
    else:
        infos = []
        for q in HINGLISH_SEARCH:
            infos += list(api.list_datasets(search=q, full=True, limit=200))
        cands = [(r, None) for r in hinglish_candidates(infos)]
    tried = []
    for repo, rev in cands[:HINGLISH_MAX_TRIES]:
        try:
            return fetch_hinglish_from(repo, rev, data, n, api, hf_hub_download, pq.read_table)
        except Exception as e:  # noqa: BLE001 - try the next candidate, keep the reason
            tried.append(f"{repo}: {type(e).__name__}: {e}"[:160])
            print(f"india_eval: hinglish candidate skipped: {tried[-1]}", file=sys.stderr)
    raise RuntimeError("no open Hinglish speech set with parquet audio + transcript found; tried: "
                       + ("; ".join(tried) if tried else "none (search found no candidate)"))


def svarah_error(e: Exception, have_token: bool) -> str:
    """Status line for a failed Svarah fetch. An access error (gated / 401 / 403) without a token leads with the fix,
    so the report's missing-groups line says what to do; the original error follows, cut to fit the report."""
    msg = f"{type(e).__name__}: {e}"
    low = msg.lower()
    if not have_token and any(k in low for k in ("gated", "401", "403", "unauthorized", "authenticated")):
        msg = f"no HF_TOKEN: {GATED_HINT}; {msg}"
    return msg[:500]


def cmd_fetch(a) -> int:
    data = Path(a.data)
    (data / a.group).mkdir(parents=True, exist_ok=True)
    status = data / a.group / "status.json"
    try:
        if a.group == "svarah":
            rows = fetch_svarah(data, a.n)
        elif a.group == "hinglish":
            rows = fetch_hinglish(data, min(a.n, HINGLISH_N))
        else:
            rows = fetch_fleurs(data, a.group, a.n)
    except Exception as e:  # noqa: BLE001 - recorded for the report, the job goes on
        if a.group not in OPTIONAL:
            raise
        err = (svarah_error(e, bool(os.environ.get("HF_TOKEN"))) if a.group == "svarah"
               else f"{type(e).__name__}: {e}"[:500])
        status.write_text(json.dumps({"ok": False, "error": err}), "utf-8")
        print(f"india_eval: {a.group} unavailable: {err}", file=sys.stderr)
        return 0
    with open(data / a.group / "manifest.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    st = {"ok": True, "clips": len(rows)}
    if a.group == "hinglish":  # which open set was used (it is found by search, not pinned)
        st["source"] = rows[0]["source"].rsplit("/row", 1)[0]
        st["license"] = rows[0].get("license", "unknown")
    status.write_text(json.dumps(st), "utf-8")
    print(f"india_eval: {len(rows)} {a.group} clips -> {data / a.group}")
    return 0


# ---------- run (offline: tinglongwu ASR + MT on the CPU) ----------

def mt_plan(group: str) -> dict:
    """Translation routes per group: name -> (text field, list of (src, tgt) hops)."""
    if group in HINDI_LIKE:
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
    asr = FasterWhisperRecognizer(md, a.whisper, "hi" if a.group in HINDI_LIKE else "en", "cpu")
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
    lang = "hi" if group in HINDI_LIKE else "en"
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
         ("svarah", "zh_direct"): "Svarah (Indian English) -> zh",
         ("hinglish", "zh_direct"): "Hinglish (Hindi-English code-switch) -> zh, direct",
         ("hinglish", "zh_pivot"): "Hinglish (Hindi-English code-switch) -> en -> zh, pivot"}


def table(rows: list[dict], missing: dict, sources: dict | None = None) -> str:
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
    for g, src in (sources or {}).items():
        lines.append(f"\n{g} source: {src}")
    return "\n".join(lines)


def cmd_report(a) -> int:
    d = Path(a.out)
    rows, missing, sources = [], {}, {}
    for g in GROUPS:
        st = d / f"{g}.status.json"
        if not st.exists():
            missing[g] = "no result"
            continue
        s = json.loads(st.read_text("utf-8"))
        if not s.get("ok"):
            missing[g] = s.get("error", "unavailable")[:160].replace("|", "/")
            continue
        if s.get("source"):
            sources[g] = f"{s['source']} (licence {s.get('license', 'unknown')})".replace("|", "/")
        rows += summarize(g, [json.loads(x) for x in (d / f"{g}.jsonl").read_text("utf-8").splitlines() if x.strip()])
    (d / "summary.json").write_text(json.dumps({"rows": rows, "missing": missing, "sources": sources}, indent=1, ensure_ascii=False),
                                    "utf-8")
    print(table(rows, missing, sources))
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
