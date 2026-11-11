#!/usr/bin/env python3
"""One-entry benchmark: ru -> Traditional Chinese speech translation quality + latency. Dev tool (run in .venv-bench).

  .venv-bench\\Scripts\\python scripts\\bench\\run.py --smoke [--engines opus,qwen3-8b,madlad3b]
  .venv-bench\\Scripts\\python scripts\\bench\\run.py --full  [--engines ...]

--smoke  20 FLEURS ru utterances -> ASR (large-v3-turbo) -> MT; chrF only. Target < 3 minutes on an RTX 3050.
--full   200 FLEURS utterances end to end (chrF + COMET) and the FLORES-200 devtest gold-text MT set (1012 sentences,
         chrF + COMET, with per-sentence latency); reuses every cached ASR/MT output from earlier runs.
Columns: opus (default engine) + --engines + google (Cloud Translation v2; skipped without key; never printed).
Engines: opus (tlwu venv .venv); qwen3-8b, qwen25-7b, qwen3-8b+ctx, qwen25-7b+ctx (llama.cpp, Q4_K_M GGUF);
         madlad3b (CTranslate2 int8_float16). See _worker.py for paths.
Cache: <data>/cache/*.jsonl keyed by (model/engine tag, sha256 of input); a rerun only computes what changed.
Latency = per-sentence wall seconds in the engine loop (model load excluded), median / p90.
Context ("+ctx" engines): previous sentence of the same FLORES article is given as context (FLORES only).
Processes are run one at a time, in order, so GPU timings are not contaminated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import _worker as W  # noqa: E402  (constants only; imports are light)

DATA = W.EVAL_DIR / "bench"
KEY_FILE = str(W.EVAL_DIR / "google_key.txt")
PY_RUNTIME = REPO / ".venv" / "Scripts" / "python.exe"
PY_BENCH = REPO / ".venv-bench" / "Scripts" / "python.exe"
RUNTIME_ENGINES = {"opus"}
ALL_ENGINES = ["opus", "qwen3-8b", "qwen3-8b+ctx", "qwen3-4b", "qwen3-4b+ctx", "qwen25-7b", "qwen25-7b+ctx", "madlad3b"]
PAIRS = {  # name -> (tlwu src, tlwu tgt, FLORES src, FLORES ref, Google src, Google tgt)
    "ru-zh": ("ru", "zh", "rus_Cyrl", "zho_Hant", "ru", "zh-TW"),
    "en-zh": ("en", "zh", "eng_Latn", "zho_Hant", "en", "zh-TW"),
    "ru-en": ("ru", "en", "rus_Cyrl", "eng_Latn", "ru", "en"),
}
PAIR = "ru-zh"  # set from --pair; non-default pairs cache under mt_<tag>.<pair>
PROMPT_V = hashlib.sha1(W.SYSTEM.encode()).hexdigest()[:6]


def cache_name(engine: str) -> str:
    return f"mt_{tag_of(engine)}" + ("" if PAIR == "ru-zh" else f".{PAIR}")


def tag_of(engine: str) -> str:
    if engine.startswith("qwen"):
        return f"{engine.split('+')[0]}-q4km-p{PROMPT_V}" + ("-ctx" if engine.endswith("+ctx") else "")
    return engine


# ---------------------------------------------------------------- data
def norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower().replace("ё", "е")
    return re.sub(r"\s+", " ", "".join(c for c in t if c.isalnum() or c.isspace())).strip()


def fleurs_rows(n: int):
    """First n distinct FLEURS ru test utterances that match a FLORES sentence: (wav_path, gold_ru, flores_ru, ref_zh_Hant)."""
    d = DATA / "flores200_dataset"
    fl = {}
    for split in ("dev", "devtest"):
        ru = (d / split / f"rus_Cyrl.{split}").read_text("utf-8").split("\n")
        zh = (d / split / f"zho_Hant.{split}").read_text("utf-8").split("\n")
        for r, z in zip(ru, zh):
            if r:
                fl.setdefault(norm(r), (r, z))
    index = {p.name: p for p in (DATA / "fleurs_ru" / "audio").rglob("*.wav")}
    rows, seen = [], set()
    for line in (DATA / "fleurs_ru" / "test.tsv").read_text("utf-8").splitlines():
        c = line.split("\t")
        k = norm(c[2])
        if k in seen or k not in fl:
            continue
        seen.add(k)
        rows.append((str(index[c[1]]), c[2], *fl[k]))
        if len(rows) >= n:
            break
    return rows


def flores_set(n: int):
    d = DATA / "flores200_dataset"
    src = (d / "devtest" / f"{PAIRS[PAIR][2]}.devtest").read_text("utf-8").split("\n")[:1012]
    ref = (d / "devtest" / f"{PAIRS[PAIR][3]}.devtest").read_text("utf-8").split("\n")[:1012]
    docs = [l.split("\t")[0] for l in (d / "metadata_devtest.tsv").read_text("utf-8").splitlines()[1:]]
    ctx = [src[i - 1] if i and docs[i] == docs[i - 1] else None for i in range(len(src))]
    return src[:n], ref[:n], ctx[:n]


# ---------------------------------------------------------------- cache
def key_of(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, root: Path, name: str):
        self.path = root / f"{name}.jsonl"
        self.d = {}
        if self.path.exists():
            for l in self.path.read_text("utf-8").splitlines():
                if l.strip():
                    r = json.loads(l)
                    self.d[r["k"]] = r
        self.root, self.name = root, name

    def add(self, rows):
        with open(self.path, "a", encoding="utf-8") as f:
            for r in rows:
                self.d[r["k"]] = r
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def meta_path(self):
        return self.root / f"{self.name}.meta.json"


def run_worker(py: Path, job: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        job = {**job, "out": str(Path(tmp) / "out.json")}
        jp = Path(tmp) / "job.json"
        jp.write_text(json.dumps(job, ensure_ascii=False), "utf-8")
        t0 = time.time()
        r = subprocess.run([str(py), "-I", str(HERE / "_worker.py"), str(jp)], cwd=str(REPO))
        if r.returncode:
            raise SystemExit(f"worker failed ({job['task']} {job.get('engine', job.get('model'))}) rc={r.returncode}")
        res = json.loads(Path(job["out"]).read_text("utf-8"))
        res["meta"]["wall_s"] = time.time() - t0
        return res


def asr(cache_root: Path, model: str, wavs: list[str], device: str):
    c = Cache(cache_root, f"asr_{model}")
    keys = [key_of(model, hashlib.sha256(Path(w).read_bytes()).hexdigest()) for w in wavs]
    miss = [i for i, k in enumerate(keys) if k not in c.d]
    if miss:
        res = run_worker(PY_RUNTIME, {"task": "asr", "model": model, "items": [wavs[i] for i in miss], "device": device})
        c.add([{"k": keys[i], "o": r["o"], "s": r["s"]} for i, r in zip(miss, res["results"])])
        c.meta_path().write_text(json.dumps(res["meta"]), "utf-8")
        print(f"  asr {model}: {len(miss)} new, {len(keys) - len(miss)} cached", flush=True)
    else:
        print(f"  asr {model}: all {len(keys)} cached", flush=True)
    return [c.d[k]["o"] for k in keys]


def mt(cache_root: Path, engine: str, items: list[dict], device: str, google_key: str | None):
    """items: [{"text", "ctx"}] -> list of {"o","s"} (cached by (tag, ctx-if-used, text))."""
    tag = tag_of(engine)
    c = Cache(cache_root, cache_name(engine))
    use_ctx = engine.endswith("+ctx")
    keys = [key_of(tag, (it["ctx"] or "") if use_ctx else "", it["text"]) for it in items]
    miss = [i for i, k in enumerate(keys) if k not in c.d]
    if miss:
        if engine == "google":
            import google_ref

            outs = google_ref.translate([items[i]["text"] for i in miss], google_key, PAIRS[PAIR][4], PAIRS[PAIR][5])
            print(f"  google chars sent: {sum(len(items[i]['text']) for i in miss)}", flush=True)
            c.add([{"k": keys[i], "o": o, "s": None} for i, o in zip(miss, outs)])
        else:
            py = PY_RUNTIME if engine in RUNTIME_ENGINES else PY_BENCH
            res = run_worker(py, {"task": "mt", "engine": engine, "items": [items[i] for i in miss],
                                  "device": device, "src": PAIRS[PAIR][0], "tgt": PAIRS[PAIR][1]})
            c.add([{"k": keys[i], "o": r["o"], "s": r["s"]} for i, r in zip(miss, res["results"])])
            c.meta_path().write_text(json.dumps(res["meta"]), "utf-8")
        print(f"  mt {engine}: {len(miss)} new, {len(keys) - len(miss)} cached", flush=True)
    else:
        print(f"  mt {engine}: all {len(keys)} cached", flush=True)
    return [c.d[k] for k in keys]


def seed_google(cache_root: Path) -> None:
    """Import Google Translation v2 outputs from the previous bench run (same input text -> same key); no API calls."""
    c = Cache(cache_root, "mt_google")
    if PAIR != "ru-zh":
        return
    pairs = [(DATA / "flores" / "src.txt", DATA / "flores" / "hyp.google.txt"),
             (DATA / "fleurs" / "asr_large-v3-turbo.txt", DATA / "fleurs" / "mt_large-v3-turbo.google.txt")]
    rows = []
    for a, b in pairs:
        if a.exists() and b.exists():
            for s, o in zip(a.read_text("utf-8").split("\n"), b.read_text("utf-8").split("\n")):
                k = key_of("google", "", s)
                if s.strip() and k not in c.d:
                    rows.append({"k": k, "o": o, "s": None})
                    c.d[k] = rows[-1]
    if rows:
        c.d = {}
        c.add(rows)
        print(f"  seeded {len(rows)} google outputs from the previous run", flush=True)


# ---------------------------------------------------------------- scoring
def pct(v: list[float], q: float) -> float:
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def score(rows: dict[str, list], srcs: list[str], refs: list[str], comet) -> dict[str, dict]:
    from sacrebleu.metrics import CHRF

    out = {}
    for name, res in rows.items():
        hyp = [r["o"] for r in res]
        d = {"chrF": CHRF().corpus_score(hyp, [refs]).score}
        if comet:
            data = [{"src": s, "mt": h, "ref": r} for s, h, r in zip(srcs, hyp, refs)]
            d["COMET"] = comet.predict(data, batch_size=16, gpus=1, progress_bar=False).system_score
        lat = [r["s"] for r in res if r.get("s") is not None]
        if lat:
            d["med_s"], d["p90_s"] = statistics.median(lat), pct(lat, 0.9)
        out[name] = d
    return out


def table(title: str, scores: dict[str, dict], cache_root: Path, with_comet: bool) -> str:
    L = [f"### {title}", "", "| engine | chrF |" + (" COMET |" if with_comet else "") + " median s/sent | p90 s | VRAM +MiB |",
         "|---|---|" + ("---|" if with_comet else "") + "---|---|---|"]
    for name, d in scores.items():
        mp = Cache(cache_root, cache_name(name)).meta_path()
        vram = json.loads(mp.read_text("utf-8")).get("vram_mib", "") if mp.exists() else ""
        L.append(f"| {name} | {d['chrF']:.1f} |" + (f" {d.get('COMET', float('nan')):.3f} |" if with_comet else "")
                 + (f" {d['med_s']:.2f} | {d['p90_s']:.2f} |" if "med_s" in d else " - | - |")
                 + f" {vram if name != 'google' else '-'} |")
    return "\n".join(L)


def other_pair(a, engines, cache_root, gkey, t_start) -> int:
    from comet import download_model, load_from_checkpoint

    n = a.flores or 50
    src, ref, _ = flores_set(n)
    items = [{"text": s, "ctx": None} for s in src]
    res = {e: mt(cache_root, e, items, a.device, gkey) for e in engines}
    if gkey:
        res["google"] = mt(cache_root, "google", items, a.device, gkey)
    comet = load_from_checkpoint(download_model("Unbabel/wmt22-comet-da"))
    print("\n" + table(f"FLORES-200 devtest {PAIR.replace('-', '->')}, gold text, first {len(src)} sentences",
                       score(res, src, ref, comet), cache_root, True))
    print(f"\nsmoke runtime: {time.time() - t_start:.0f} s")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--smoke", action="store_true")
    g.add_argument("--full", action="store_true")
    ap.add_argument("--engines", default="", help=f"comma list of extra engines besides opus: {','.join(ALL_ENGINES[1:])}")
    ap.add_argument("--pair", default="ru-zh", choices=list(PAIRS),
                    help="ru-zh (default; FLEURS + FLORES). en-zh / ru-en: FLORES devtest gold-text smoke only, "
                         "first --flores sentences (default 50 for these pairs), chrF + COMET")
    ap.add_argument("--no-google", action="store_true", help="omit the Google column (no API calls)")
    ap.add_argument("--n", type=int, help="override the number of FLEURS utterances (smoke 20, full 200)")
    ap.add_argument("--flores", type=int, default=None, help="--full: FLORES devtest sentences (0 = skip)")
    ap.add_argument("--asr-model", default="large-v3-turbo")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--cache", default=str(DATA / "cache"))
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    global PAIR
    PAIR = a.pair
    cache_root = Path(a.cache)
    cache_root.mkdir(parents=True, exist_ok=True)
    extra = [e for e in a.engines.split(",") if e]
    for e in extra:
        if e not in ALL_ENGINES:
            raise SystemExit(f"unknown engine {e}; choose from {ALL_ENGINES}")
    engines = list(dict.fromkeys(["opus", *extra]))
    gkey = None
    if not a.no_google and Path(KEY_FILE).exists():
        gkey = Path(KEY_FILE).read_text("utf-8").strip()
        seed_google(cache_root)
    t_start = time.time()
    if PAIR != "ru-zh":
        return other_pair(a, engines, cache_root, gkey, t_start)
    if a.flores is None:
        a.flores = 1012
    comet = None
    n = a.n or (20 if a.smoke else 200)
    rows = fleurs_rows(n)
    print(f"FLEURS ru: {len(rows)} utterances, ASR {a.asr_model}", flush=True)
    hyp_ru = asr(cache_root, a.asr_model, [r[0] for r in rows], a.device)
    items = [{"text": t, "ctx": None} for t in hyp_ru]
    res = {}
    for e in engines:
        res[e] = mt(cache_root, e, items, a.device, gkey)
    if gkey:
        res["google"] = mt(cache_root, "google", items, a.device, gkey)
    refs = [r[3] for r in rows]
    srcs = [r[2] for r in rows]
    if a.full:
        from comet import download_model, load_from_checkpoint
        comet = load_from_checkpoint(download_model("Unbabel/wmt22-comet-da"))
    out = [table(f"FLEURS ru, {len(rows)} utterances, ASR {a.asr_model} -> MT (ref FLORES zho_Hant)",
                 score(res, srcs, refs, comet), cache_root, a.full)]
    if a.full and a.flores:
        src, ref, ctx = flores_set(a.flores)
        fitems = [{"text": s, "ctx": c} for s, c in zip(src, ctx)]
        fres = {}
        for e in engines:
            fres[e] = mt(cache_root, e, fitems, a.device, gkey)
        if gkey:
            fres["google"] = mt(cache_root, "google", fitems, a.device, gkey)
        out.append(table(f"FLORES-200 devtest ru->zho_Hant, gold text, {len(src)} sentences",
                         score(fres, src, ref, comet), cache_root, True))
    print("\n" + "\n\n".join(out))
    print(f"\n{'smoke' if a.smoke else 'full'} runtime: {time.time() - t_start:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
