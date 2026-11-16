#!/usr/bin/env python3
"""#1198: Hy-MT2 CPU translation time per line under the live load, for several thread plans.

In the live app the speech model (Whisper, CTranslate2) and the translation model (Hy-MT2, llama.cpp) run at the
same time on different threads: Whisper transcribes the next segment while Hy-MT2 translates the previous one.
`tlwu_eval --smoke` runs them one after the other, so it never shows what happens when both thread pools together
ask for more threads than the CPU has (#1198: 7.6 s per line on an 8-core / 16-thread Ryzen 7040).

This bench translates the gold source lines of the smoke set (no ASR errors, so chrF compares only the MT path)
while a background thread keeps Whisper busy on the smoke clips, once per thread plan, and prints one Markdown
table: MT median / p90 seconds per line, chrF against the references, and Whisper seconds per clip during the run.

  python scripts/bench/mt_cpu_threads.py --data eval_data/smoke --models-dir ~/tlwu-models [--whisper small]
  python scripts/bench/mt_cpu_threads.py ... --plans old,cpu-mode --no-asr     # MT alone, no background Whisper
  python scripts/bench/mt_cpu_threads.py ... --plans cpu-mode --asr-load live   # Whisper paced like live audio

--asr-load (#1198): `busy` (default) keeps Whisper transcribing back to back, a worst case the app never reaches
while Whisper keeps up with the audio. `live` starts each clip only when its audio would have arrived (real time),
like the live pipeline's final pass per segment; the table's Whisper column then also shows how busy it was.
  python scripts/bench/mt_cpu_threads.py ... --plans cpu-mode --quants Q4_K_M,Q3_K_M,Q2_K  # lower-bit Hy-MT2 GGUFs

--quants (#1198 next cut): run each plan once per quantization of the same Hy-MT2 model. Q4_K_M is the shipped
file from --models-dir; any other quant is read from --quant-dir, filled beforehand by `--fetch-quants` (a separate
run: importing tinglongwu blocks the network, so the download step never imports it). Bench only: not pinned, never
shipped. A lower-bit file is only worth shipping if its MT median under load is lower with a chrF drop <= 2 on the
same lines.

--make-quants (#1198): tencent/Hy-MT2-1.8B-GGUF only has a few quants, so any quant still missing in --quant-dir is
made locally with llama.cpp's own quantizer (llama_cpp.llama_model_quantize) from the best source in --quant-dir/source
(F32 > BF16 > F16 > Q8_0, put there by --fetch-quants when the repo has one), else from the shipped Q4_K_M
(re-quantized: same speed, quality is a lower bound). The table names the source, e.g. "Q3_K_M (self from F16)".

Plans (asr / mt decode / mt batch threads; P = physical cores, L = logical CPUs):
  old       the pre-#1198 CPU mode: asr = min(L-1, 8), mt = L//2, batch = L (llama-cpp-python defaults)
  cpu-mode  tinglongwu.hwprobe.cpu_mode_threads(L): what tlwu uses now (asr = L//2, mt = batch = L - asr)
  physical  asr = max(1, P//2), mt = batch = max(1, P - asr)
  mt-heavy  asr = max(1, L//4), mt = batch = max(1, L - asr)   (MT gets 3/4 of the CPU, Whisper the rest)
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PAIRS = (("en", "zh"), ("ja", "zh"), ("ru", "zh"), ("zh", "en"))
SHIPPED_QUANT = "Q4_K_M"  # tinglongwu.hymt.HYMT_FILE
GGUF_REPO = "tencent/Hy-MT2-1.8B-GGUF"
SOURCE_PREFS = ("F32", "BF16", "F16", "Q8_0")  # quantization sources, best first
SAMPLE_RATE = 16000  # tinglongwu.capture.base.SAMPLE_RATE (smoke clips are 16 kHz mono)
SELF_PREFIX = "self-from-"  # --quant-dir/<SELF_PREFIX><source>/: files made by --make-quants
# llama.h `enum llama_ftype` (llama-cpp-python 0.3.36 vendor copy)
FTYPES = {"Q4_0": 2, "Q4_1": 3, "Q8_0": 7, "Q5_0": 8, "Q5_1": 9, "Q2_K": 10, "Q3_K_S": 11, "Q3_K_M": 12,
          "Q3_K_L": 13, "Q4_K_S": 14, "Q4_K_M": 15, "Q5_K_S": 16, "Q5_K_M": 17, "Q6_K": 18, "IQ4_NL": 25,
          "IQ3_M": 27, "IQ4_XS": 30}


def pick_quants(repo_files: list[str], quants: list[str]) -> dict[str, str | None]:
    """quant -> GGUF file name in the repo (None = not there). Matches `...-<quant>.gguf` / `..._<quant>.gguf`,
    case-insensitive; a split model (`-00001-of-...`) and imatrix side files are never picked."""
    out: dict[str, str | None] = {}
    for q in quants:
        hit = None
        for f in sorted(repo_files):
            name = f.rsplit("/", 1)[-1].lower()
            if not name.endswith(".gguf") or "-of-" in name or "imatrix" in name:
                continue
            stem = name[:-5]
            if stem.endswith("-" + q.lower()) or stem.endswith("_" + q.lower()) or stem.endswith("." + q.lower()):
                hit = f
                break
        out[q] = hit
    return out


def pick_source(repo_files: list[str]) -> tuple[str, str] | None:
    """(quant label, file) of the best quantization source in a file list (SOURCE_PREFS order), None if none."""
    got = pick_quants(repo_files, list(SOURCE_PREFS))
    return next(((q, got[q]) for q in SOURCE_PREFS if got[q]), None)


def quant_label(quant: str, path: str | None) -> str:
    """Table label: the quant, plus its source when --make-quants made it."""
    parent = Path(path).parent.name if path else ""
    return f"{quant} (self from {parent[len(SELF_PREFIX):]})" if parent.startswith(SELF_PREFIX) else quant


def plans(physical: int, logical: int) -> dict[str, tuple[int, int, int]]:
    from tinglongwu.hwprobe import cpu_mode_threads

    P, L = max(1, physical), max(1, logical)
    cm = cpu_mode_threads(L)
    heavy_asr = max(1, L // 4)
    return {"old": (max(1, min(L - 1, 8)), max(1, L // 2), L),
            "cpu-mode": (cm.asr, cm.mt, cm.mt_batch),
            "physical": (max(1, P // 2), max(1, P - P // 2), max(1, P - P // 2)),
            "mt-heavy": (heavy_asr, max(1, L - heavy_asr), max(1, L - heavy_asr))}


def mt_lines(rows: list[dict], per_pair: int) -> list[tuple[str, str, str, str]]:
    """(src, tgt, source text, reference) for the focus pairs, gold transcripts as input."""
    out = []
    for src, tgt in PAIRS:
        got = [r for r in rows if r.get("lang") == src and r.get(f"ref_{tgt}") and r.get("ref_text")][:per_pair]
        out += [(src, tgt, r["ref_text"], r[f"ref_{tgt}"]) for r in got]
    return out


class AsrLoad:
    """Keeps Whisper (beam 1, like CPU mode) transcribing smoke clips on its own thread until stopped.

    paced=False (worst case): clips back to back, Whisper never idles.
    paced=True (live, #1198): a clip is transcribed only once its audio has "arrived", i.e. clip i starts at
    max(previous clip done, start + total seconds of clips 0..i), like the live pipeline where the final pass of
    a segment runs once the speaker finished it. Whisper slower than real time runs back to back (it lags)."""

    def __init__(self, mdir: Path | None, size: str, threads: int, wavs: list[str], paced: bool = False,
                 asr=None, audio=None, clock=time.perf_counter, wait=None):
        if asr is None:
            from tinglongwu.asr import FasterWhisperRecognizer

            asr = FasterWhisperRecognizer(mdir, size, "auto", "cpu", beam=1, threads=threads)
        if audio is None:
            from tlwu_eval.runner import read_wav

            audio = [read_wav(w)[0] for w in wavs]
        self.asr, self.audio, self.paced, self.clock = asr, audio, paced, clock
        self.stop = threading.Event()
        self.wait = wait or self.stop.wait
        self.spans: list[tuple[float, float]] = []  # (start, end) clock time of each clip
        self.error: BaseException | None = None
        self.t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        i = 0
        t_start, arrived = self.clock(), 0.0
        try:
            while not self.stop.is_set():
                clip = self.audio[i % len(self.audio)]
                if self.paced:
                    arrived += len(clip) / SAMPLE_RATE
                    due = t_start + arrived - self.clock()
                    if due > 0 and self.wait(due):
                        break  # stopped while waiting for the audio
                t0 = self.clock()
                self.asr.transcribe(clip)
                self.spans.append((t0, self.clock()))
                i += 1
        except BaseException as e:  # noqa: BLE001 - re-raised by __exit__: a bench without its load is invalid
            self.error = e

    def during(self, start: float, end: float) -> list[float]:
        """Seconds per clip for clips that ran entirely inside [start, end] (the MT measurement window)."""
        return [b - a for a, b in self.spans if a >= start and b <= end]

    def busy(self, start: float, end: float) -> float:
        """Share of [start, end] during which Whisper was transcribing (0..1)."""
        if end <= start:
            return 0.0
        return sum(max(0.0, min(b, end) - max(a, start)) for a, b in self.spans) / (end - start)

    def __enter__(self):
        self.asr.transcribe(self.audio[0])  # warm-up outside the measurement
        self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.t.join()
        if self.error is not None and exc[0] is None:
            raise RuntimeError(f"background Whisper failed, MT numbers are not under load: {self.error}") \
                from self.error


def translate_all(llm, lines) -> tuple[list[float], list[tuple[str, str]]]:
    from tinglongwu.engines import clean_output, max_tokens_for
    from tinglongwu.hymt import build_prompt, sampling, zh_convert

    times, pairs = [], []
    for src, tgt, text, ref in lines:
        t0 = time.perf_counter()
        r = llm.create_chat_completion([{"role": "user", "content": build_prompt(text, src, tgt, [])}],
                                       max_tokens=max_tokens_for(text), **sampling())
        times.append(time.perf_counter() - t0)
        out = clean_output(r["choices"][0]["message"]["content"] or "")
        pairs.append((ref, zh_convert(out, "tw") if tgt == "zh" else out))
    return times, pairs


def fetch_quants(repo: str, quants: list[str], dest: Path) -> None:
    """Download the requested quants of `repo` into `dest` (bench only: no pin, never used by tlwu itself).
    Runs without importing tinglongwu (its network guard would block the download)."""
    from huggingface_hub import hf_hub_download, list_repo_files

    files = list_repo_files(repo)
    print(f"mt_cpu_threads: {repo} GGUFs: {', '.join(f for f in files if f.lower().endswith('.gguf'))}",
          file=sys.stderr, flush=True)
    got = pick_quants(files, quants)
    for q, f in got.items():
        if f is not None:
            hf_hub_download(repo, f, local_dir=str(dest))
        print(f"mt_cpu_threads: {q}: {f or 'not in ' + repo}", file=sys.stderr, flush=True)
    src = pick_source(files)
    if any(f is None for f in got.values()) and src is not None:
        hf_hub_download(repo, src[1], local_dir=str(Path(dest) / "source"))
        print(f"mt_cpu_threads: quantization source {src[0]}: {src[1]}", file=sys.stderr, flush=True)


def _llama_quantize(src: str, dst: str, ftype: int, requantize: bool) -> int:
    import llama_cpp

    llama_cpp.llama_backend_init()
    params = llama_cpp.llama_model_quantize_default_params()
    params.ftype = ftype
    params.allow_requantize = requantize
    return llama_cpp.llama_model_quantize(src.encode(), dst.encode(), params)


def make_quants(quant_dir: Path, quants: list[str], fallback: str, quantize=_llama_quantize) -> dict[str, str | None]:
    """Make every quant that --quant-dir lacks from the best local source (see module doc); returns quant -> path
    (None = unknown quant type or the quantizer failed). `quantize(src, dst, ftype, requantize) -> 0 on success`."""
    quant_dir = Path(quant_dir)
    srcdir = quant_dir / "source"
    local = local_quants(srcdir, list(SOURCE_PREFS)) if srcdir.is_dir() else {}
    src = next(((q, local[q]) for q in SOURCE_PREFS if local.get(q)), None)
    label, src_path = src if src else (SHIPPED_QUANT, fallback)
    have = local_quants(quant_dir, quants)  # never the source/ files
    out: dict[str, str | None] = {}
    for q in quants:
        if have.get(q):
            out[q] = have[q]
            continue
        if q.upper() == label.upper() or q.upper() not in FTYPES:
            out[q] = None
            print(f"mt_cpu_threads: {q}: cannot make it from {label}", file=sys.stderr, flush=True)
            continue
        dst = quant_dir / f"{SELF_PREFIX}{label}" / f"Hy-MT2-1.8B-{q.upper()}.gguf"
        dst.parent.mkdir(parents=True, exist_ok=True)
        rc = quantize(str(src_path), str(dst), FTYPES[q.upper()], label not in ("F32", "BF16", "F16"))
        out[q] = str(dst) if rc == 0 and dst.is_file() else None
        print(f"mt_cpu_threads: {q}: {'made from ' + label if out[q] else f'quantize failed (rc={rc})'}",
              file=sys.stderr, flush=True)
    return out


def local_quants(quant_dir: Path | None, quants: list[str]) -> dict[str, str | None]:
    """quant -> local GGUF path under `quant_dir` (searched recursively), None when missing."""
    if quant_dir is None or not Path(quant_dir).is_dir():
        return {q: None for q in quants}
    found = {str(p.relative_to(quant_dir)): p for p in sorted(Path(quant_dir).rglob("*"))
             if p.is_file() and p.suffix.lower() == ".gguf" and "source" not in p.relative_to(quant_dir).parts[:-1]}
    return {q: (None if f is None else str(found[f])) for q, f in pick_quants(list(found), quants).items()}


def p90(v: list[float]) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", default="eval_data/smoke")
    p.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS"))
    p.add_argument("--whisper", default="small")
    p.add_argument("--plans", default="old,cpu-mode,physical")
    p.add_argument("--per-pair", type=int, default=5)
    p.add_argument("--no-asr", action="store_true", help="MT alone (no background Whisper)")
    p.add_argument("--asr-load", choices=("busy", "live"), default="busy",
                   help="busy: Whisper back to back (worst case); live: each clip once its audio has arrived (#1198)")
    p.add_argument("--quants", default=SHIPPED_QUANT,
                   help=f"comma list of Hy-MT2 GGUF quantizations (default {SHIPPED_QUANT}, the shipped file)")
    p.add_argument("--gguf-repo", default=GGUF_REPO, help="Hugging Face repo for quants other than the shipped one")
    p.add_argument("--quant-dir", type=Path, default=None, help="where --fetch-quants put the other quants")
    p.add_argument("--fetch-quants", action="store_true",
                   help="only download the non-shipped --quants from --gguf-repo into --quant-dir, then exit")
    p.add_argument("--make-quants", action="store_true",
                   help="only quantize the --quants missing in --quant-dir (llama.cpp quantizer), then exit")
    a = p.parse_args(argv)
    quants = [x.strip() for x in a.quants.split(",") if x.strip()] or [SHIPPED_QUANT]
    others = [q for q in quants if q.upper() != SHIPPED_QUANT]
    if a.fetch_quants:
        if a.quant_dir is None:
            p.error("--fetch-quants needs --quant-dir")
        fetch_quants(a.gguf_repo, others, a.quant_dir)
        return 0
    if a.make_quants:
        if a.quant_dir is None:
            p.error("--make-quants needs --quant-dir")
        from tinglongwu.hymt import hymt_path
        from tinglongwu.models import models_dir

        made = make_quants(a.quant_dir, others, str(hymt_path(models_dir(a.models_dir))))
        return 0 if all(made.values()) else 1
    # The plans are listen-only thread counts: TLWU_BOTH=1 would make Whisper use the #250 listen+speak split instead
    # of the counts printed in the table.
    os.environ.pop("TLWU_BOTH", None)

    import psutil
    from llama_cpp import Llama
    from tinglongwu.hymt import hymt_path
    from tinglongwu.models import models_dir
    from tlwu_eval import scoring
    from tlwu_eval.runner import load_manifest

    mdir = models_dir(a.models_dir)
    L = os.cpu_count() or 1
    P = psutil.cpu_count(logical=False) or L
    rows = load_manifest(a.data)
    lines = mt_lines(rows, a.per_pair)
    wavs = [r["wav"] for r in rows if r.get("wav")][:4]
    if not lines:
        print("mt_cpu_threads: no gold lines with references in the manifest", file=sys.stderr)
        return 2
    allp = plans(P, L)
    print(f"CPU: {P} physical cores, {L} logical; {len(lines)} lines; background Whisper "
          f"{'off' if a.no_asr else a.whisper + ' beam 1, ' + a.asr_load + ' load'}\n")
    files = {SHIPPED_QUANT: str(hymt_path(mdir)), **local_quants(a.quant_dir, others)}
    out = ["| plan | quant | asr / mt / mt batch threads | MT median s/line | MT p90 s | chrF "
           "| Whisper s/clip (during MT) |",
           "|---|---|---|---|---|---|---|"]
    seen = set()
    for name, quant in [(n, q) for n in [x.strip() for x in a.plans.split(",") if x.strip()] for q in quants]:
        asr_t, mt_t, mt_b = allp[name]
        path = files.get(SHIPPED_QUANT if quant.upper() == SHIPPED_QUANT else quant)
        if path is None or not Path(path).is_file():
            out.append(f"| {name} | {quant} | {asr_t} / {mt_t} / {mt_b} | not found | | | |")
            continue
        quant = quant_label(quant, path)
        if (asr_t, mt_t, mt_b, path) in seen:
            out.append(f"| {name} | {quant} | {asr_t} / {mt_t} / {mt_b} | same as above | | | |")
            continue
        seen.add((asr_t, mt_t, mt_b, path))
        llm = Llama(model_path=path, n_gpu_layers=0, n_ctx=2048, n_threads=mt_t,
                    n_threads_batch=mt_b, verbose=False)
        translate_all(llm, lines[:1])  # warm-up
        if a.no_asr:
            times, pairs = translate_all(llm, lines)
            asr_s = "-"
        else:
            with AsrLoad(mdir, a.whisper, asr_t, wavs, paced=a.asr_load == "live") as load:
                w0 = time.perf_counter()
                times, pairs = translate_all(llm, lines)
                w1 = time.perf_counter()
            inside = load.during(w0, w1)
            busy = load.busy(w0, w1)
            del load  # free this plan's Whisper before the next plan loads its own
            asr_s = f"{statistics.median(inside):.2f} ({busy:.0%} busy)" if inside else f"- ({busy:.0%} busy)"
        del llm
        out.append(f"| {name} | {quant} | {asr_t} / {mt_t} / {mt_b} | {statistics.median(times):.2f} | {p90(times):.2f} "
                   f"| {scoring.chrf(pairs):.2f} | {asr_s} |")
        print(out[-1], file=sys.stderr, flush=True)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
