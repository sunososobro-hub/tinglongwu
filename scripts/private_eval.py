#!/usr/bin/env python3
"""#1408: WER of the owner's private recordings after each update, numbers only.

The recordings never enter this public repo. They live in a local folder named by the env var
TLWU_PRIVATE_EVAL_DIR (outside the repo) on the owner PC that runs `hw-tests`:

    NAME.wav        the recording
    NAME.ref.txt    reference transcript (UTF-8)
    NAME.lang       optional, source language code (default: ru)
    NAME.partial    optional, empty marker: the reference covers only part of the audio (scripts/wer.py --partial)

    python scripts/private_eval.py run [--dir D] [--models-dir M] [--partial] [--summary FILE]
    python scripts/private_eval.py bootstrap NAME.wav [--models-dir M] [--force]

`run` transcribes every pair with the shipped ASR path (`python -m tinglongwu --file`, no --model: the same
model choice a fresh install makes) and prints one line per file: its index in sorted order, duration, WER and
the change against the previous run. Nothing else leaves the folder: no file names, paths or transcript text in
stdout, stderr or the summary (tlwu's own output is discarded). The previous run is kept in
`.private_eval_history.json` inside the folder. A file (or the total) more than REGRESSION_PTS points worse than
the previous run prints `WARN private eval regressed`; this is informational and never fails the step.
Env var unset, folder missing or no pairs -> `SKIP private eval`, exit 0.

`bootstrap` writes a draft NAME.ref.txt with the largest downloaded Whisper model, so the owner only proofreads it.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import wer as wer_mod  # noqa: E402  (scripts/wer.py: same tokenizer and --partial alignment)

ENV = "TLWU_PRIVATE_EVAL_DIR"
HISTORY = ".private_eval_history.json"
DEFAULT_LANG = "ru"
REGRESSION_PTS = 2.0  # owner's #1408 Acceptance: "worse than the previous run by > 2.0 points"
LARGEST_FIRST = ["large-v3", "large-v3-turbo", "medium", "small", "base", "tiny"]


def pairs(folder: Path) -> list[dict]:
    """Sorted NAME.wav files that have a NAME.ref.txt next to them."""
    out = []
    for wav in sorted(folder.glob("*.wav")):
        ref = wav.with_name(wav.stem + ".ref.txt")
        if not ref.is_file():
            continue
        lang_f = wav.with_name(wav.stem + ".lang")
        lang = (lang_f.read_text("utf-8").strip() if lang_f.is_file() else "") or DEFAULT_LANG
        out.append({"key": wav.stem, "wav": wav, "ref": ref, "lang": lang,
                    "partial": wav.with_name(wav.stem + ".partial").is_file()})
    return out


def duration_s(wav: Path) -> float:
    try:
        with wave.open(str(wav), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, OSError):
        try:
            import soundfile as sf

            return float(sf.info(str(wav)).duration)
        except Exception:  # noqa: BLE001
            return 0.0


def transcribe(wav: Path, lang: str, models_dir: str | None = None, model: str | None = None) -> str:
    """Run the shipped CLI on one file and return the joined source text. tlwu's output is discarded (it may
    name the file); a failure raises without any of it."""
    with tempfile.TemporaryDirectory(prefix="tlwu-pe-") as td:
        out = Path(td) / "lines.jsonl"
        cmd = [sys.executable, "-m", "tinglongwu", "--file", str(wav), "--save", str(out), "--lang", lang,
               "--transcript-only"]
        if models_dir:
            cmd += ["--models-dir", models_dir]
        if model:
            cmd += ["--model", model]
        rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc != 0:
            raise RuntimeError(f"tlwu exited {rc}")
        return join_lines(out.read_text("utf-8") if out.is_file() else "")


def join_lines(jsonl: str) -> str:
    texts = []
    for line in jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("src"), str):
            texts.append(row["src"])
    return " ".join(texts)


def score(ref_text: str, hyp_text: str, lang: str, partial: bool = False) -> dict:
    """scripts/wer.py scoring. `partial`: the reference covers only part of the audio, so the transcript is longer;
    the reference is aligned to its best-matching transcript window (wer.py's semi-global alignment, roles swapped:
    wer.py --partial is for a transcript that covers only part of the reference)."""
    if not partial:
        return wer_mod.score(ref_text, hyp_text, lang)
    ref, hyp = wer_mod.tokenize(ref_text, lang), wer_mod.tokenize(hyp_text, lang)
    if not ref:
        raise ValueError("empty reference")
    metric = "CER" if lang in wer_mod.CHAR_LANGS else "WER"
    if not hyp:
        return {"metric": metric, "rate": 1.0, "errors": len(ref), "ref_units": len(ref)}
    _s, _e, errs = wer_mod.best_window(hyp, ref)
    return {"metric": metric, "rate": errs / len(ref), "errors": errs, "ref_units": len(ref)}


def load_history(folder: Path) -> dict:
    try:
        h = json.loads((folder / HISTORY).read_text("utf-8"))
        return h if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


def _fmt_delta(now: float, prev: float | None) -> str:
    return "new" if prev is None else f"{(now - prev) * 100:+.2f} pt"


def run(folder: Path | None, models_dir: str | None = None, partial: bool = False, summary: Path | None = None,
        transcriber=transcribe) -> int:
    if folder is None or not folder.is_dir():
        print("SKIP private eval (TLWU_PRIVATE_EVAL_DIR not set or not a folder)")
        return 0
    items = pairs(folder)
    if not items:
        print("SKIP private eval (no NAME.wav + NAME.ref.txt pairs)")
        return 0
    prev = load_history(folder)
    prev_files = prev.get("files") if isinstance(prev.get("files"), dict) else {}
    rows, files, errs, units, regressed = [], {}, 0, 0, False
    for i, it in enumerate(items, 1):
        dur = duration_s(it["wav"])
        try:
            hyp = transcriber(it["wav"], it["lang"], models_dir)
            r = score(it["ref"].read_text("utf-8"), hyp, it["lang"], partial or it["partial"])
        except Exception as e:  # noqa: BLE001  (message may hold a path: print only its type)
            rows.append(f"file {i}: {dur:7.1f} s  error ({type(e).__name__})")
            continue
        p = prev_files.get(it["key"], {}).get("rate") if isinstance(prev_files.get(it["key"]), dict) else None
        reg = p is not None and (r["rate"] - p) * 100 > REGRESSION_PTS
        regressed |= reg
        files[it["key"]] = {"rate": r["rate"], "metric": r["metric"]}
        errs += r["errors"]
        units += r["ref_units"]
        rows.append(f"file {i}: {dur:7.1f} s  {r['metric']} {r['rate'] * 100:6.2f}%  "
                    f"{_fmt_delta(r['rate'], p)}" + ("  REGRESSED" if reg else ""))
    total = errs / units if units else None
    p_total = prev.get("total") if isinstance(prev.get("total"), (int, float)) else None
    if total is not None and p_total is not None and (total - p_total) * 100 > REGRESSION_PTS:
        regressed = True
    lines = rows + [f"total: {len(files)}/{len(items)} files  "
                    + (f"{total * 100:.2f}%  {_fmt_delta(total, p_total)}" if total is not None else "no score")]
    if regressed:
        lines.append(f"WARN private eval regressed (> {REGRESSION_PTS:.1f} pt worse than the previous run)")
    for line in lines:
        print(line)
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### #1408 private eval\n```\n" + "\n".join(lines) + "\n```\n")
    if files:  # keep earlier numbers of files that failed this time
        merged = dict(prev_files)
        merged.update(files)
        (folder / HISTORY).write_text(json.dumps({"files": merged, "total": total}, indent=1), "utf-8")
    return 0


def largest_model(models_dir: str | None) -> str | None:
    from tinglongwu.models import models_dir as mdir_of, whisper_installed

    have = whisper_installed(mdir_of(models_dir))
    return next((m for m in LARGEST_FIRST if m in have), None)


def bootstrap(wav: Path, models_dir: str | None = None, force: bool = False, transcriber=transcribe,
              model: str | None = None) -> int:
    ref = wav.with_name(wav.stem + ".ref.txt")
    if ref.exists() and not force:
        print("bootstrap: a reference already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    model = model or largest_model(models_dir)
    if model is None:
        print("bootstrap: no Whisper model downloaded (run tlwu-setup)", file=sys.stderr)
        return 1
    lang_f = wav.with_name(wav.stem + ".lang")
    lang = (lang_f.read_text("utf-8").strip() if lang_f.is_file() else "") or DEFAULT_LANG
    text = transcriber(wav, lang, models_dir, model)
    ref.write_text(text.strip() + "\n", "utf-8")
    print(f"bootstrap: draft reference written with {model} ({len(text.split())} words); proofread it once")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dir", default=None, help=f"recordings folder (default: env {ENV})")
    r.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS"))
    r.add_argument("--partial", action="store_true", help="every reference covers only part of its audio")
    r.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    b = sub.add_parser("bootstrap")
    b.add_argument("wav")
    b.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS"))
    b.add_argument("--model", default=None, choices=LARGEST_FIRST, help="default: the largest downloaded one")
    b.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        d = a.dir or os.environ.get(ENV) or ""
        return run(Path(d) if d.strip() else None, a.models_dir, a.partial, Path(a.summary) if a.summary else None)
    return bootstrap(Path(a.wav), a.models_dir, a.force, model=a.model)


if __name__ == "__main__":
    sys.exit(main())
