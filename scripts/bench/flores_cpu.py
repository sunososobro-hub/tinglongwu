#!/usr/bin/env python3
"""#606: Hy-MT2-1.8B (the shipped Q4_K_M GGUF, llama.cpp on the CPU) on the full FLORES-200 devtest -> zho_Hant.

Dev tool (not part of the offline runtime). Runs on a cloud CPU runner (`.github/workflows/eval-flores.yml`) or any PC:

  python scripts/bench/fetch_data.py --out DATA --fleurs ""          # FLORES-200 only
  TLWU_HYMT_TEMP=0 python scripts/bench/flores_cpu.py --data DATA/flores200_dataset --models-dir MODELS \
      --src rus_Cyrl --out OUT [--limit N] [--no-comet]
  python scripts/bench/flores_cpu.py --matrix kor_Hang,vie_Latn   # JSON list for the workflow matrix (#656)
  python scripts/bench/flores_cpu.py --temps 0.7,0.2,0             # JSON list of Hy-MT2 temperatures (#107)
  python scripts/bench/flores_cpu.py ... --src rus_Cyrl --tgt eng_Latn --limit 200   # any 6x6 direction (#1305)
  python scripts/bench/flores_cpu.py --matrix6                     # JSON list of the 6x6 languages (#1305)
  python scripts/bench/flores_cpu.py --table OUT [OUT ...]         # 6x6 Markdown table from the summaries (#1305)

Writes OUT/<src>.jsonl (one line per sentence: i, src, ref, hyp, sec, chrf, comet) and OUT/<src>.summary.json
(n, chrF, COMET, median/p90 s per sentence, CPU, Hy-MT2 temperature, lines containing U+FFFD). chrF = sacrebleu CHRF corpus score, COMET = Unbabel/wmt22-comet-da
system score, the same metrics as docs/benchmarks.md. Latency is per-sentence wall time, model load excluded.
A rerun with the same OUT resumes: sentences already in the .jsonl are not translated again.
--temp T sets TLWU_HYMT_TEMP for this run (#107: 0.7 = model-card default, 0 = greedy); without it the
environment (or the 0.7 default) decides. fffd_lines counts hypotheses with a U+FFFD replacement character (#632).
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
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# FLORES code -> tlwu language code (Hy-MT2 direct src -> zh, OpenCC to Traditional, tw variant)
LANGS = {"rus_Cyrl": "ru", "eng_Latn": "en", "jpn_Jpan": "ja",
         # #656: Korean (tier 1) + tier-2 languages (docs/VISION.md)
         "kor_Hang": "ko", "vie_Latn": "vi", "tha_Thai": "th", "ind_Latn": "id", "spa_Latn": "es",
         "deu_Latn": "de", "fra_Latn": "fr"}
REF = "zho_Hant"  # default target: the shipped X -> zh-Hant rows of docs/benchmarks.md
SPLIT = "devtest"
# #1305: the source x target language-pair matrix (en / ru / zh-Hans / zh-Hant / ja / ko, 30 directions).
MATRIX6 = ["eng_Latn", "rus_Cyrl", "zho_Hans", "zho_Hant", "jpn_Jpan", "kor_Hang"]
SOURCES = {**LANGS, "zho_Hans": "zh", "zho_Hant": "zh"}
# FLORES target -> (tlwu language code, zh variant for tinglongwu.translate.zh_convert: tw = Traditional, cn = Simplified)
TARGETS = {"zho_Hant": ("zh", "tw"), "zho_Hans": ("zh", "cn"), "eng_Latn": ("en", "tw"), "rus_Cyrl": ("ru", "tw"),
           "jpn_Jpan": ("ja", "tw"), "kor_Hang": ("ko", "tw")}
NAMES = {"eng_Latn": "en", "rus_Cyrl": "ru", "zho_Hans": "zh-Hans", "zho_Hant": "zh-Hant", "jpn_Jpan": "ja",
         "kor_Hang": "ko"}
# directions the app ships today (docs/benchmarks.md X -> zh-Hant rows); the weakest of them in the same run is the
# "weak" line of the 6x6 table
SHIPPED = [("eng_Latn", "zho_Hant"), ("rus_Cyrl", "zho_Hant"), ("jpn_Jpan", "zho_Hant"), ("kor_Hang", "zho_Hant")]


def read_lines(path: Path) -> list[str]:
    t = path.read_text("utf-8").split("\n")
    return t[:-1] if t and t[-1] == "" else t


def stem(src: str, tgt: str = REF) -> str:
    """OUT file stem: <src> for the classic X -> zh-Hant rows (unchanged names), <src>-<tgt> for other targets."""
    return src if tgt == REF else f"{src}-{tgt}"


class ScriptConvert:
    """#1305: zh-Hans <-> zh-Hant is no MT step in the app (Hy-MT2 returns zh text unchanged for zh -> zh); the
    output script comes from tinglongwu.translate.zh_convert (OpenCC), so the matrix measures exactly that."""

    engine = "OpenCC (tinglongwu.translate.zh_convert, no MT)"

    def __init__(self, variant: str, convert=None):
        if convert is None:
            sys.path.insert(0, str(REPO))
            from tinglongwu.translate import zh_convert as convert
        self.variant, self.convert = variant, convert

    def translate(self, text, src, tgt):
        return types.SimpleNamespace(text=self.convert(text, self.variant))


def load_pair(data: Path, src: str, limit: int | None = None, tgt: str = REF) -> list[tuple[str, str]]:
    s = read_lines(data / SPLIT / f"{src}.{SPLIT}")
    r = read_lines(data / SPLIT / f"{tgt}.{SPLIT}")
    if len(s) != len(r):
        raise SystemExit(f"flores_cpu: {src} has {len(s)} lines but {tgt} has {len(r)}")
    rows = list(zip(s, r))
    return rows[:limit] if limit else rows


def translate_all(rows, tr, src_code: str, jsonl: Path, clock=time.perf_counter, log=print,
                  temp: float | None = None, tgt_code: str = "zh") -> list[dict]:
    """Translate rows not yet in `jsonl` (resume), append each result as it is done; return all rows in order.
    Each new row records the Hy-MT2 `temp`; a resume at another temperature stops instead of mixing them (#107)."""
    done: dict[int, dict] = {}
    if jsonl.exists():
        for line in jsonl.read_text("utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if temp is not None and d.get("temp", temp) != temp:
                    raise SystemExit(f"flores_cpu: {jsonl} was translated at temperature {d['temp']}, not {temp}; "
                                     "use another --out")
                done[d["i"]] = d
    with jsonl.open("a", encoding="utf-8") as f:
        for i, (s, r) in enumerate(rows):
            if i in done:
                continue
            if tr is None:
                raise SystemExit(f"flores_cpu: --score-only but sentence {i} is missing from {jsonl}")
            t0 = clock()
            hyp = tr.translate(s, src_code, tgt_code).text.strip()
            d = {"i": i, "src": s, "ref": r, "hyp": hyp, "sec": round(clock() - t0, 4)}
            if temp is not None:
                d["temp"] = temp
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            f.flush()
            done[i] = d
            if (i + 1) % 50 == 0:
                log(f"flores_cpu: {i + 1}/{len(rows)}", flush=True)
    return [done[i] for i in range(len(rows))]


def matrix(spec: str) -> list[str]:
    """#656: comma list of FLORES codes (workflow input `langs`) -> validated list for the job matrix."""
    out = [x.strip() for x in spec.split(",") if x.strip()]
    bad = [x for x in out if x not in LANGS]
    if bad or not out:
        raise SystemExit(f"flores_cpu: unknown or empty langs {bad or spec!r}; known: {','.join(LANGS)}")
    return list(dict.fromkeys(out))


def table(summaries: list[dict]) -> str:
    """#1305: 6x6 Markdown table (rows = source, columns = target), each cell `COMET / chrF`. A cell whose COMET is
    below the weakest shipped direction (SHIPPED) of the same run is marked weak; a missing cell is `-`."""
    cell = {}
    for s in summaries:
        a, _, b = str(s.get("pair", "")).partition("->")
        if a in MATRIX6 and b in MATRIX6 and a != b:
            cell[(a, b)] = s
    shipped = [cell[p]["COMET"] for p in SHIPPED if p in cell and cell[p].get("COMET") is not None]
    weak = min(shipped) if shipped else None
    rows = ["| source \\ target | " + " | ".join(NAMES[t] for t in MATRIX6) + " |",
            "|---" * (len(MATRIX6) + 1) + "|"]
    for a in MATRIX6:
        out = []
        for b in MATRIX6:
            s = cell.get((a, b))
            if a == b:
                out.append("")
            elif s is None or s.get("COMET") is None:
                out.append("-")
            else:
                mark = " (weak)" if weak is not None and s["COMET"] < weak else ""
                out.append(f"{s['COMET']:.3f} / {s['chrF']:.1f}{mark}")
        rows.append(f"| **{NAMES[a]}** | " + " | ".join(out) + " |")
    n = {s.get("n") for s in cell.values()}
    foot = (f"\n{len(cell)}/30 cells, n = {', '.join(str(x) for x in sorted(n, key=str))} sentences per cell; weak = COMET "
            f"below the weakest shipped direction ({weak:.3f})" if weak is not None else
            f"\n{len(cell)}/30 cells; weak line unknown (no shipped X -> zh-Hant cell in this run)")
    return "\n".join(rows) + "\n" + foot + "\n"


def read_summaries(dirs: list[str]) -> list[dict]:
    return [json.loads(p.read_text("utf-8")) for d in dirs for p in sorted(Path(d).rglob("*.summary.json"))]


def temps(spec: str) -> list[str]:
    """#107: comma list of Hy-MT2 temperatures (workflow input `temps`) -> validated list for the job matrix."""
    out = [x.strip() for x in spec.split(",") if x.strip()]
    try:
        ok = bool(out) and all(0.0 <= float(x) <= 2.0 for x in out)
    except ValueError:
        ok = False
    if not ok:
        raise SystemExit(f"flores_cpu: temps must be numbers in 0..2, got {spec!r}")
    return list(dict.fromkeys(out))


def temperature() -> float:
    """The Hy-MT2 temperature a translator built now would use (tinglongwu.hymt.sampling(), env override)."""
    sys.path.insert(0, str(REPO))
    from tinglongwu.hymt import sampling

    return float(sampling()["temperature"])


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]


def score(out: list[dict], comet=None) -> dict:
    """Corpus chrF (+ per-sentence chrF) and, when a COMET model is given, system + per-sentence COMET."""
    from sacrebleu.metrics import CHRF

    hyp, ref = [d["hyp"] for d in out], [d["ref"] for d in out]
    res = {"n": len(out), "chrF": round(CHRF().corpus_score(hyp, [ref]).score, 2),
           "fffd_lines": sum("\ufffd" in h for h in hyp)}  # #107/#632: broken-UTF-8 output
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


def make_translator(models_dir: Path, zh_variant: str = "tw"):
    sys.path.insert(0, str(REPO))
    from tinglongwu.hymt import HyMtTranslator

    tr = HyMtTranslator(models_dir, zh_variant, "cpu")
    tr.preload()
    return tr


def main(argv=None, make=make_translator, load_comet=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", required=True, help="flores200_dataset dir (from fetch_data.py)")
    ap.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS", ""), help="default: $TLWU_MODELS")
    ap.add_argument("--src", required=True, choices=sorted(SOURCES))
    ap.add_argument("--tgt", default=REF, choices=sorted(TARGETS), help=f"FLORES target (default {REF}, #1305)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="first N sentences only (0 = all 1012)")
    ap.add_argument("--no-comet", action="store_true")
    ap.add_argument("--score-only", action="store_true", help="score OUT/<src>.jsonl, translate nothing")
    ap.add_argument("--temp", type=float, default=None, help="Hy-MT2 temperature (sets TLWU_HYMT_TEMP)")
    ap.add_argument("--comet-model", default="Unbabel/wmt22-comet-da")
    a = ap.parse_args(argv)
    if not a.score_only and not a.models_dir:
        ap.error("--models-dir (or TLWU_MODELS) is needed to translate")
    if a.temp is not None:
        if not 0.0 <= a.temp <= 2.0:
            ap.error("--temp must be in 0..2")
        os.environ["TLWU_HYMT_TEMP"] = repr(a.temp)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.src == a.tgt:
        ap.error("--src and --tgt are the same language")
    src_code, (tgt_code, variant) = SOURCES[a.src], TARGETS[a.tgt]
    convert = src_code == tgt_code  # zh-Hans <-> zh-Hant: script conversion, no Hy-MT2 (ScriptConvert)
    rows = load_pair(Path(a.data), a.src, a.limit or None, a.tgt)
    jsonl = out_dir / f"{stem(a.src, a.tgt)}.jsonl"
    summary = out_dir / f"{stem(a.src, a.tgt)}.summary.json"
    temp = None if a.score_only or convert else temperature()  # the COMET venv (--score-only) has no tlwu to ask
    if a.score_only:
        tr = None
    elif convert:
        tr = ScriptConvert(variant)
    else:
        tr = make(Path(a.models_dir)) if variant == "tw" else make(Path(a.models_dir), zh_variant=variant)
    t0 = time.perf_counter()
    out = translate_all(rows, tr, src_code, jsonl, temp=temp, tgt_code=tgt_code)
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
    engine = ScriptConvert.engine if convert else "Hy-MT2-1.8B Q4_K_M (llama.cpp, CPU)"
    res.update(pair=f"{a.src}->{a.tgt}", split=SPLIT, engine=prev.get("engine", engine) if a.score_only else engine,
               temperature=prev.get("temperature") if a.score_only else temp,
               cpu=prev.get("cpu") or cpu_name(), translate_wall_s=prev.get("translate_wall_s", round(wall, 1)))
    jsonl.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in out), "utf-8")
    summary.write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--matrix"]:  # eval-flores.yml plan job: print the JSON list for `strategy.matrix.src`
        print(json.dumps(matrix(sys.argv[2] if len(sys.argv) > 2 else "")))
        raise SystemExit(0)
    if sys.argv[1:2] == ["--matrix6"]:  # eval-flores.yml plan job, matrix=6x6 (#1305)
        print(json.dumps(MATRIX6))
        raise SystemExit(0)
    if sys.argv[1:2] == ["--table"]:  # eval-flores.yml table job (#1305): summaries of every 6x6 job -> Markdown
        print(table(read_summaries(sys.argv[2:])), end="")
        raise SystemExit(0)
    if sys.argv[1:2] == ["--temps"]:  # eval-flores.yml plan job: JSON list for `strategy.matrix.temp` (#107)
        print(json.dumps(temps(sys.argv[2] if len(sys.argv) > 2 else "")))
        raise SystemExit(0)
    raise SystemExit(main())
