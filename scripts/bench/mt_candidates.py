#!/usr/bin/env python3
"""#1319 (#745 part 4): CPU A/B of smaller candidate MT models against the shipped Hy-MT2 1.8B Q4_K_M.

Every CPU knob of the current stack is measured (#1222, #1310); MT on the CPU (~2.9 s/line) caps the gain, so this
bench asks whether a smaller MT model is worth it. Bench only: candidates are never pinned, shipped or used by tlwu.

  python scripts/bench/mt_candidates.py --fetch --dir cand                 # download the candidate GGUFs (network)
  python scripts/bench/mt_candidates.py --run --dir cand --data eval_data/matrix_pins --models-dir ~/tlwu-models
  python scripts/bench/mt_candidates.py --comet --dir cand                 # optional, in a venv with unbabel-comet
  python scripts/bench/mt_candidates.py --table --dir cand                 # Markdown table

--run: Whisper (CPU mode for this PC: size from --whisper, beam / threads from hwprobe.cpu_mode_settings) transcribes
--clips clips per pair once; every model then translates the same ASR lines with CPU mode's MT threads
(hwprobe.cpu_mode_threads), so the rows differ only by the MT model. Per line: MT seconds, end to end = ASR + MT
seconds (like tlwu_eval's latency_s). Writes DIR/<model>.json (one per model: per-clip src / asr / ref / mt / timings).
The baseline uses the app's own prompt and sampling (tinglongwu.hymt); candidates get the same instruction text
in their own chat format with greedy decoding, and the same output cleanup + OpenCC (zh -> Taiwan) as the app.
--fetch / --comet never import tinglongwu (its network guard blocks Hugging Face; COMET pins its own torch).
--table: per model and pair, MT ms/line p50, end-to-end p50, chrF with a bootstrap 95% interval (clips resampled,
#1293), COMET (n/a when --comet did not run), and the overall chrF change vs the baseline with a paired bootstrap
interval (scripts/bench/cpu_mode_ab.chrf_drop_ci).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BASELINE = "hymt2-1.8b-q4km"
PAIRS = (("en", "zh"), ("ja", "zh"), ("ru", "zh"), ("zh", "en"))
# Openly licensed, downloadable without a login, smaller than (or as small as) the 1.8B baseline. Sizes from the
# repos' file lists; the table prints the downloaded file's real size.
CANDIDATES = {
    "qwen2.5-0.5b": {"repo": "Qwen/Qwen2.5-0.5B-Instruct-GGUF", "quant": "Q4_K_M", "license": "Apache-2.0",
                     "fmt": "chatml"},
    "qwen3-0.6b": {"repo": "Qwen/Qwen3-0.6B-GGUF", "quant": "Q8_0", "license": "Apache-2.0", "fmt": "chatml-nothink"},
    "qwen2.5-1.5b": {"repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "quant": "Q4_K_M", "license": "Apache-2.0",
                     "fmt": "chatml"},
}
BASELINE_INFO = {"repo": "tencent/Hy-MT2-1.8B-GGUF", "quant": "Q4_K_M", "license": "Tencent Hunyuan (shipped)"}
STOPS = ["<|im_end|>", "<|endoftext|>"]


def parse_names(text: str | None) -> list[str]:
    """Candidate names from a comma list (empty = all). Unknown names are an error, so a typo never runs nothing."""
    names = [x.strip() for x in (text or "").split(",") if x.strip()] or list(CANDIDATES)
    bad = [n for n in names if n not in CANDIDATES]
    if bad:
        raise ValueError(f"unknown candidate(s): {', '.join(bad)} (known: {', '.join(CANDIDATES)})")
    return names


def chat_prompt(instruction: str, fmt: str) -> str:
    """One user turn in the model's chat format, ending where the assistant's answer starts."""
    if fmt not in ("chatml", "chatml-nothink"):
        raise ValueError(f"unknown chat format {fmt}")
    p = f"<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n"
    # Qwen3's template with enable_thinking=False: an empty think block, so the model answers directly
    return p + "<think>\n\n</think>\n\n" if fmt == "chatml-nothink" else p


def select_clips(rows: list[dict], pairs, per_pair: int) -> list[tuple[str, str, dict]]:
    """(src, tgt, row) for the first `per_pair` clips (by id) of each pair that have a reference."""
    out = []
    for src, tgt in pairs:
        got = sorted((r for r in rows if r.get("lang") == src and r.get(f"ref_{tgt}")), key=lambda r: r["id"])
        out += [(src, tgt, r) for r in got[:per_pair]]
    return out


def missing_clips(clips, pairs, per_pair: int) -> dict[str, int]:
    """'src->tgt' -> how many clips that pair lacks (only pairs with fewer than per_pair)."""
    have = {f"{s}->{t}": 0 for s, t in pairs}
    for s, t, _row in clips:
        if f"{s}->{t}" in have:
            have[f"{s}->{t}"] += 1
    return {p: per_pair - n for p, n in have.items() if n < per_pair}


def p50(v):
    return statistics.median(v) if v else None


def chrf_ci(pairs: list[tuple[str, str]], chrf, n: int = 1000, seed: int = 0) -> tuple[float, float, float] | None:
    """(chrF, lo, hi): corpus chrF and its 95% bootstrap percentile interval (lines resampled with replacement)."""
    if not pairs:
        return None
    rng = random.Random(seed)
    boots = sorted(chrf([rng.choice(pairs) for _ in pairs]) for _ in range(n))
    return chrf(pairs), boots[int(0.025 * (n - 1))], boots[int(round(0.975 * (n - 1)))]


def as_report(res: dict) -> dict:
    """The shape scripts/bench/cpu_mode_ab.chrf_drop_ci reads: languages[{pair, chrf, clips[{id, ref_mt, mt}]}]."""
    return {"languages": [{"pair": lg["pair"], "chrf": None,
                           "clips": [{"id": c["id"], "ref_mt": c["ref"], "mt": c["mt"]} for c in lg["clips"]]}
                          for lg in res["languages"]]}


def summarize(res: dict, chrf) -> dict:
    """{pair: {mt_ms, e2e_s, chrf, lo, hi, comet, n}, '_all': {...}}; '_all' chrF = mean of the pairs' corpus chrF
    (as cpu_mode_ab.summarize), its interval from resampling within each pair."""
    out, per = {}, {}
    for lg in res["languages"]:
        cl = lg["clips"]
        pairs = [(c["ref"], c["mt"]) for c in cl]
        per[lg["pair"]] = pairs
        ci = chrf_ci(pairs, chrf)
        out[lg["pair"]] = {"mt_ms": p50([1000 * c["mt_s"] for c in cl]), "e2e_s": p50([c["asr_s"] + c["mt_s"] for c in cl]),
                           "chrf": ci and ci[0], "lo": ci and ci[1], "hi": ci and ci[2],
                           "comet": lg.get("comet"), "n": len(cl)}
    allc = [c for lg in res["languages"] for c in lg["clips"]]
    rng, n = random.Random(0), 1000
    boots = sorted(statistics.fmean(chrf([rng.choice(p) for _ in p]) for p in per.values() if p) for _ in range(n)) \
        if any(per.values()) else []
    chrfs = [v["chrf"] for v in out.values() if v["chrf"] is not None]
    comets = [v["comet"] for v in out.values() if v["comet"] is not None]
    out["_all"] = {"mt_ms": p50([1000 * c["mt_s"] for c in allc]), "e2e_s": p50([c["asr_s"] + c["mt_s"] for c in allc]),
                   "chrf": statistics.fmean(chrfs) if chrfs else None,
                   "lo": boots[int(0.025 * (n - 1))] if boots else None,
                   "hi": boots[int(round(0.975 * (n - 1)))] if boots else None,
                   "comet": statistics.fmean(comets) if len(comets) == len(chrfs) and comets else None,
                   "n": len(allc)}
    return out


def _f(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}"


def table(results: dict[str, dict], chrf, drop_ci) -> str:
    """Markdown: one row per model and pair (+ overall), then the models (licence, file, size) and the chrF change
    of each candidate vs the baseline with its paired bootstrap interval."""
    if BASELINE not in results:
        raise ValueError(f"no {BASELINE} result: the table compares against it")
    order = [BASELINE] + sorted(n for n in results if n != BASELINE)
    sums = {n: summarize(results[n], chrf) for n in order}
    pairs = [p for p in sums[BASELINE] if p != "_all"]
    for n in order:
        if [p for p in sums[n] if p != "_all"] != pairs:
            raise ValueError(f"{n} covers different pairs than {BASELINE}")
    base_all = sums[BASELINE]["_all"]
    lines = ["| model | pair | MT ms/line p50 | end-to-end s p50 | chrF [95% CI] | COMET | lines |",
             "|---|---|---|---|---|---|---|"]
    for n in order:
        for p in pairs + ["_all"]:
            s = sums[n][p]
            lines.append(f"| {n} | {'**all**' if p == '_all' else p} | {_f(s['mt_ms'], 0)} | {_f(s['e2e_s'], 2)} "
                         f"| {_f(s['chrf'])} [{_f(s['lo'])}, {_f(s['hi'])}] | {_f(s['comet'], 4)} | {s['n']} |")
    lines += ["", "| model | repo / quant | licence | file MB | MT p50 vs baseline | e2e p50 vs baseline "
                  "| chrF change vs baseline [paired 95% CI] |", "|---|---|---|---|---|---|---|"]
    for n in order:
        r, s = results[n], sums[n]["_all"]
        mt = "-" if n == BASELINE or not base_all["mt_ms"] else f"{100 * (s['mt_ms'] / base_all['mt_ms'] - 1):+.0f}%"
        e2e = "-" if n == BASELINE or not base_all["e2e_s"] else f"{100 * (s['e2e_s'] / base_all['e2e_s'] - 1):+.0f}%"
        if n == BASELINE:
            dc = "-"
        else:
            ci = drop_ci(as_report(results[BASELINE]), as_report(r), chrf=chrf)
            dc = "n/a" if ci is None else f"{-ci['drop']:+.2f} [{-ci['hi']:+.2f}, {-ci['lo']:+.2f}]"
        lines.append(f"| {n} | {r.get('repo', '?')} {r.get('quant', '')} | {r.get('license', '?')} "
                     f"| {_f(r.get('size_mb'), 0)} | {mt} | {e2e} | {dc} |")
    return "\n".join(lines)


# ---- --fetch (no tinglongwu import) ----

def fetch(names: list[str], dest: Path, hub=None) -> dict[str, str | None]:
    """Download each candidate's GGUF into dest/<name>/; name -> local path (None = no matching file in the repo)."""
    from mt_cpu_threads import pick_quants

    if hub is None:
        import huggingface_hub as hub
    got = {}
    for n in names:
        c = CANDIDATES[n]
        f = pick_quants(hub.list_repo_files(c["repo"]), [c["quant"]])[c["quant"]]
        got[n] = hub.hf_hub_download(c["repo"], f, local_dir=str(dest / n)) if f else None
        print(f"mt_candidates: {n}: {c['repo']} {c['quant']} -> {got[n] or 'not found'}", file=sys.stderr, flush=True)
    return got


def local_file(dest: Path, name: str) -> Path | None:
    files = sorted((dest / name).rglob("*.gguf")) if (dest / name).is_dir() else []
    return files[0] if files else None


# ---- --run ----

class LlamaMT:
    """translate(text, src, tgt) -> (text, seconds) with one GGUF; baseline = the app's Hy-MT2 prompt + sampling."""

    def __init__(self, path: Path, fmt: str | None, threads: int, batch: int, llama=None):
        if llama is None:
            from llama_cpp import Llama as llama
        self.llm = llama(model_path=str(path), n_gpu_layers=0, n_ctx=2048, n_threads=threads,
                         n_threads_batch=batch, verbose=False)
        self.fmt = fmt

    def translate(self, text: str, src: str, tgt: str, clock=time.perf_counter) -> tuple[str, float]:
        from tinglongwu.engines import clean_output, max_tokens_for
        from tinglongwu.hymt import build_prompt, sampling, zh_convert

        instr = build_prompt(text, src, tgt, [])
        t0 = clock()
        if self.fmt is None:
            r = self.llm.create_chat_completion([{"role": "user", "content": instr}],
                                               max_tokens=max_tokens_for(text), **sampling())
            raw = r["choices"][0]["message"]["content"] or ""
        else:
            r = self.llm.create_completion(chat_prompt(instr, self.fmt), max_tokens=max_tokens_for(text),
                                           temperature=0.0, top_k=1, repeat_penalty=1.05, stop=STOPS)
            raw = r["choices"][0]["text"] or ""
        dt = clock() - t0
        out = clean_output(raw)
        return (zh_convert(out, "tw") if tgt == "zh" else out), dt


def transcribe_all(clips, mdir: Path, whisper: str, beam: int, threads: int, clock=time.perf_counter) -> list[dict]:
    """[{pair, id, src, ref, asr, asr_s}] with Whisper on the CPU, one recognizer per source language."""
    from tinglongwu.asr import FasterWhisperRecognizer
    from tlwu_eval.runner import read_wav

    out, recs = [], {}
    for src, tgt, row in clips:
        if src not in recs:
            recs[src] = FasterWhisperRecognizer(mdir, whisper, src, "cpu", beam=beam, threads=threads)
        audio, _dur = read_wav(row["wav"])
        t0 = clock()
        tr = recs[src].transcribe(audio)
        out.append({"pair": f"{src}->{tgt}", "id": row["id"], "src": src, "tgt": tgt, "ref": row[f"ref_{tgt}"],
                    "asr": (getattr(tr, "text", "") or "").strip(), "asr_s": clock() - t0})
        print(f"mt_candidates: ASR {row['id']} {out[-1]['asr_s']:.2f} s", file=sys.stderr, flush=True)
    return out


def translate_all(lines: list[dict], mt) -> dict:
    """Result dict (pair groups in input order) for one model over the shared ASR lines."""
    if lines:
        mt.translate(lines[0]["asr"] or "Hello.", lines[0]["src"], lines[0]["tgt"])  # warm-up, not timed
    langs: dict[str, dict] = {}
    for ln in lines:
        text, dt = mt.translate(ln["asr"], ln["src"], ln["tgt"]) if ln["asr"] else ("", 0.0)
        langs.setdefault(ln["pair"], {"pair": ln["pair"], "clips": []})["clips"].append(
            {"id": ln["id"], "src_text": ln["asr"], "ref": ln["ref"], "mt": text.strip(), "asr_s": round(ln["asr_s"], 4),
             "mt_s": round(dt, 4)})
    return {"languages": list(langs.values())}


def run(a, names: list[str]) -> int:
    os.environ.pop("TLWU_BOTH", None)  # listen-only thread plan, as in mt_cpu_threads.py
    from tinglongwu import offline
    from tinglongwu.hwprobe import cpu_mode_settings, cpu_mode_threads, ram_mib
    from tinglongwu.hymt import hymt_path
    from tinglongwu.models import models_dir
    from tlwu_eval.runner import load_manifest

    offline.set_mode("listen")
    mdir = models_dir(a.models_dir)
    cores = os.cpu_count() or 4
    mib = ram_mib()
    st, plan = cpu_mode_settings(cores, mib / 1024 if mib else 16.0), cpu_mode_threads(cores)
    pairs = [tuple(p.split("->")) for p in a.pairs.split(",") if p.strip()]
    clips = select_clips(load_manifest(a.data), pairs, a.clips)
    short = missing_clips(clips, pairs, a.clips)
    if short:  # every requested pair needs its full clip count, else the rows compare different sets
        print("mt_candidates: too few clips with references: "
              + ", ".join(f"{p} missing {n}" for p, n in short.items()), file=sys.stderr)
        return 2
    print(f"mt_candidates: {cores} CPUs; Whisper {a.whisper} beam {st.beam} threads {st.threads}; "
          f"MT threads {plan.mt}/{plan.mt_batch}; {len(clips)} clips", file=sys.stderr, flush=True)
    lines = transcribe_all(clips, mdir, a.whisper, st.beam, st.threads)
    models = [(BASELINE, hymt_path(mdir), None, BASELINE_INFO)]
    for n in names:
        path = local_file(a.dir, n)
        if path is None:
            print(f"mt_candidates: {n}: no GGUF in {a.dir / n} (run --fetch first); skipped", file=sys.stderr)
            continue
        models.append((n, path, CANDIDATES[n]["fmt"], CANDIDATES[n]))
    for stale in a.dir.glob("*.json"):  # an earlier run's report would be compared with this run's baseline
        stale.unlink()
    for name, path, fmt, info in models:
        mt = LlamaMT(path, fmt, plan.mt, plan.mt_batch)
        res = translate_all(lines, mt)
        del mt
        res.update(model=name, file=Path(path).name, size_mb=round(Path(path).stat().st_size / 2**20, 1),
                   repo=info["repo"], quant=info["quant"], license=info["license"],
                   threads={"asr": st.threads, "mt": plan.mt, "mt_batch": plan.mt_batch}, cpus=cores)
        (a.dir / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
        print(f"mt_candidates: {name} done", file=sys.stderr, flush=True)
    return 0


# ---- --comet (no tinglongwu import) ----

def add_comet(dest: Path, model, gpus: int = 0) -> list[str]:
    """Adds per-clip `comet` and per-pair system `comet` (x100, like docs/benchmarks.md) to every DIR/*.json."""
    done = []
    for f in sorted(dest.glob("*.json")):
        res = json.loads(f.read_text("utf-8"))
        cl = [c for lg in res["languages"] for c in lg["clips"]]
        pred = model.predict([{"src": c["src_text"], "mt": c["mt"], "ref": c["ref"]} for c in cl],
                             batch_size=16, gpus=gpus, progress_bar=False)
        for c, s in zip(cl, pred.scores):
            c["comet"] = round(100 * float(s), 4)
        for lg in res["languages"]:
            lg["comet"] = round(statistics.fmean(c["comet"] for c in lg["clips"]), 4) if lg["clips"] else None
        f.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
        done.append(f.stem)
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--comet", action="store_true")
    mode.add_argument("--table", action="store_true")
    p.add_argument("--dir", type=Path, default=Path("cand"))
    p.add_argument("--candidates", default="", help=f"comma list (default all: {', '.join(CANDIDATES)})")
    p.add_argument("--data", default="eval_data/matrix_pins")
    p.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS"))
    p.add_argument("--whisper", default="small")
    p.add_argument("--pairs", default=",".join(f"{s}->{t}" for s, t in PAIRS))
    p.add_argument("--clips", type=int, default=20, help="clips per pair")
    a = p.parse_args(argv)
    try:
        names = parse_names(a.candidates)
    except ValueError as e:
        p.error(str(e))
    a.dir.mkdir(parents=True, exist_ok=True)
    if a.fetch:
        return 0 if all(fetch(names, a.dir).values()) else 1
    if a.run:
        return run(a, names)
    if a.comet:
        from comet import download_model, load_from_checkpoint

        print("mt_candidates: COMET on " + ", ".join(add_comet(a.dir, load_from_checkpoint(
            download_model("Unbabel/wmt22-comet-da")))), file=sys.stderr)
        return 0
    from cpu_mode_ab import chrf_drop_ci
    from tlwu_eval.scoring import chrf

    results = {f.stem: json.loads(f.read_text("utf-8")) for f in sorted(a.dir.glob("*.json"))}
    try:
        print(table(results, chrf, chrf_drop_ci))
    except ValueError as e:
        print(f"mt_candidates: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
