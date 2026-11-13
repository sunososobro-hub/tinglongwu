#!/usr/bin/env python3
"""#606: Hy-MT2-1.8B (the shipped Q4_K_M GGUF, llama.cpp on the CPU) on the full FLORES-200 devtest -> zho_Hant.

Dev tool (not part of the offline runtime). Runs on a cloud CPU runner (`.github/workflows/eval-flores.yml`) or any PC:

  python scripts/bench/fetch_data.py --out DATA --fleurs ""          # FLORES-200 only
  TLWU_HYMT_TEMP=0 python scripts/bench/flores_cpu.py --data DATA/flores200_dataset --models-dir MODELS \
      --src rus_Cyrl --out OUT [--limit N] [--no-comet]

Writes OUT/<src>.jsonl (one line per sentence: i, src, ref, hyp, sec, chrf, comet) and OUT/<src>.summary.json
(n, chrF, COMET, median/p90 s per sentence, CPU). chrF = sacrebleu CHRF corpus score, COMET = Unbabel/wmt22-comet-da
system score, the same metrics as docs/benchmarks.md. Latency is per-sentence wall time, model load excluded.
A rerun with the same OUT resumes: sentences already in the .jsonl are not translated again.
--score-only scores an existing complete .jsonl without loading tinglongwu or Hy-MT2, so COMET (which pins its own
torch/numpy) can run in a separate venv: translate with --no-comet in the tlwu env, then --score-only in the COMET venv.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# FLORES code -> tlwu language code (Hy-MT2 direct src -> zh, OpenCC to Traditional, tw variant)
LANGS = {"rus_Cyrl": "ru", "eng_Latn": "en", "jpn_Jpan": "ja"}
REF = "zho_Hant"
SPLIT = "devtest"


def read_lines(path: Path) -> list[str]:
    t = path.read_text("utf-8").split("\n")
    return t[:-1] if t and t[-1] == "" else t


def load_pair(data: Path, src: str, limit: int | None = None) -> list[tuple[str, str]]:
    s = read_lines(data / SPLIT / f"{src}.{SPLIT}")
    r = read_lines(data / SPLIT / f"{REF}.{SPLIT}")
    if len(s) != len(r):
        raise SystemExit(f"flores_cpu: {src} has {len(s)} lines but {REF} has {len(r)}")
    rows = list(zip(s, r))
    return rows[:limit] if limit else rows


def translate_all(rows, tr, src_code: str, jsonl: Path, clock=time.perf_counter, log=print) -> list[dict]:
    """Translate rows not yet in `jsonl` (resume), append each result as it is done; return all rows in order."""
    done: dict[int, dict] = {}
    if jsonl.exists():
        for line in jsonl.read_text("utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done[d["i"]] = d
    with jsonl.open("a", encoding="utf-8") as f:
        for i, (s, r) in enumerate(rows):
            if i in done:
                continue
            if tr is None:
                raise SystemExit(f"flores_cpu: --score-only but sentence {i} is missing from {jsonl}")
            t0 = clock()
            hyp = tr.translate(s, src_code, "zh").text.strip()
            d = {"i": i, "src": s, "ref": r, "hyp": hyp, "sec": round(clock() - t0, 4)}
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            f.flush()
            done[i] = d
            if (i + 1) % 50 == 0:
                log(f"flores_cpu: {i + 1}/{len(rows)}", flush=True)
    return [done[i] for i in range(len(rows))]


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]


def score(out: list[dict], comet=None) -> dict:
    """Corpus chrF (+ per-sentence chrF) and, when a COMET model is given, system + per-sentence COMET."""
    from sacrebleu.metrics import CHRF

    hyp, ref = [d["hyp"] for d in out], [d["ref"] for d in out]
    res = {"n": len(out), "chrF": round(CHRF().corpus_score(hyp, [ref]).score, 2)}
    m = CHRF()
    for d in out:
        d["chrf"] = round(m.sentence_score(d["hyp"], [d["ref"]]).score, 2)
    if comet is not None:
        pred = comet.predict([{"src": d["src"], "mt": d["hyp"], "ref": d["ref"]} for d in out],
                             batch_size=16, gpus=0, progress_bar=False)
        res["COMET"] = round(float(pred.system_score), 4)
        for d, c in zip(out, pred.scores):
            d["comet"] = round(float(c), 4)
    secs = [d["sec"] for d in out]
    res["median_s"] = round(statistics.median(secs), 3) if secs else None
    res["p90_s"] = round(p90(secs), 3) if secs else None
    return res


def cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def make_translator(models_dir: Path):
    sys.path.insert(0, str(REPO))
    from tinglongwu.hymt import HyMtTranslator

    tr = HyMtTranslator(models_dir, "tw", "cpu")
    tr.preload()
    return tr


def main(argv=None, make=make_translator, load_comet=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", required=True, help="flores200_dataset dir (from fetch_data.py)")
    ap.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS", ""), help="default: $TLWU_MODELS")
    ap.add_argument("--src", required=True, choices=sorted(LANGS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="first N sentences only (0 = all 1012)")
    ap.add_argument("--no-comet", action="store_true")
    ap.add_argument("--score-only", action="store_true", help="score OUT/<src>.jsonl, translate nothing")
    ap.add_argument("--comet-model", default="Unbabel/wmt22-comet-da")
    a = ap.parse_args(argv)
    if not a.score_only and not a.models_dir:
        ap.error("--models-dir (or TLWU_MODELS) is needed to translate")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = load_pair(Path(a.data), a.src, a.limit or None)
    jsonl = out_dir / f"{a.src}.jsonl"
    summary = out_dir / f"{a.src}.summary.json"
    t0 = time.perf_counter()
    out = translate_all(rows, None if a.score_only else make(Path(a.models_dir)), LANGS[a.src], jsonl)
    wall = time.perf_counter() - t0
    # --score-only keeps the translate run's wall time and CPU from its summary
    prev = json.loads(summary.read_text("utf-8")) if a.score_only and summary.exists() else {}
    comet = None
    if not a.no_comet:
        if load_comet is None:
            from comet import download_model, load_from_checkpoint

            comet = load_from_checkpoint(download_model(a.comet_model))
        else:
            comet = load_comet(a.comet_model)
    res = score(out, comet)
    res.update(pair=f"{a.src}->{REF}", split=SPLIT, engine="Hy-MT2-1.8B Q4_K_M (llama.cpp, CPU)",
               cpu=prev.get("cpu") or cpu_name(), translate_wall_s=prev.get("translate_wall_s", round(wall, 1)))
    jsonl.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in out), "utf-8")
    summary.write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
