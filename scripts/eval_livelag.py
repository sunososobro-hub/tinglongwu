#!/usr/bin/env python3
"""Live subtitle lag on a busy low-end CPU (#1369): does Listen mode stay close to the present?

Usage:
  python scripts/eval_livelag.py build --data eval_data/smoke --out livelag.wav
      3 min English clip: the smoke-set English clips looped back to back, with 8 s of synthetic music at 90 s.
  python scripts/eval_livelag.py run --audio livelag.wav --models-dir DIR [--busy 1] [--out livelag.json]
      Keeps --busy cores spinning in the background, runs `tlwu --file <audio> --paced --latency-jsonl ...`
      (real time, like live audio) and scores the records it wrote.
  python scripts/eval_livelag.py score --jsonl records.jsonl
      Scores an existing --latency-jsonl file.

Lag at T s = T minus the audio position (end of speech) of the newest translated line on screen at T, averaged over
the 20 s before T (lines come once per segment, so one instant swings by a segment). Lines written untranslated to
catch up (`mt_skipped`) do not count as translated. Pass: lag at 60 s <= 8 s, lag at 180 s no more than 2 s above
lag at 60 s, and no shown line is a music sound tag ("Music.", "[MUSIC PLAYING]"). Exit 0 pass, 1 fail, 2 error.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LAG_AT_60_MAX = 8.0  # #1369 Acceptance
GROWTH_MAX = 2.0  # lag(180) - lag(60)
WINDOW_S = 20
CLIP_S = 180.0
MUSIC_AT_S, MUSIC_S = 90.0, 8.0
SR = 16000


def load_jsonl(path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text("utf-8").splitlines() if x.strip()]


def lag(records: list[dict], at_s: float) -> float | None:
    """Mean on-screen age of the newest translated line over the WINDOW_S seconds before at_s; None if no
    translated line was shown by the start of that window."""
    shown = sorted((r["at"], r["at"] - r["total"]) for r in records if not r.get("mt_skipped") and "at" in r)
    ages = []
    for k in range(WINDOW_S):
        t = at_s - k
        eos = [e for when, e in shown if when <= t]
        if not eos:
            return None
        ages.append(t - max(eos))
    return sum(ages) / len(ages)


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def shown_lags(records: list[dict]) -> tuple[float | None, float | None]:
    """#1532: median seconds from end of speech to the source line on screen (`t_source_shown`) and to its
    translation (`total`, translated lines only). None when no record has it (a sink that shows no source)."""
    src = [r["t_source_shown"] for r in records if r.get("t_source_shown") is not None]
    mt = [r["total"] for r in records if not r.get("mt_skipped") and r.get("total") is not None]
    return _median(src), _median(mt)


def score(records: list[dict], clip_s: float = CLIP_S) -> dict:
    from tinglongwu.hallucination import is_music_tag

    l60, lend = lag(records, 60.0), lag(records, clip_s)
    music = [r["text"] for r in records if is_music_tag(r.get("text") or "")]
    fails = []
    if l60 is None or lend is None:
        fails.append("no translated line by the checkpoint")
    else:
        if l60 > LAG_AT_60_MAX:
            fails.append(f"lag at 60 s {l60:.1f} s > {LAG_AT_60_MAX:.0f} s")
        if lend - l60 > GROWTH_MAX:
            fails.append(f"lag grew {lend - l60:.1f} s from 60 s to {clip_s:.0f} s (> {GROWTH_MAX:.0f} s)")
    if music:
        fails.append(f"{len(music)} music-only line(s) shown")
    r = lambda v: None if v is None else round(v, 2)  # noqa: E731
    src_s, mt_s = shown_lags(records)
    return {"lag_60_s": r(l60), f"lag_{clip_s:.0f}_s": r(lend), "src_shown_s": r(src_s), "mt_shown_s": r(mt_s),
            "lines": len(records),
            "skipped": sum(1 for x in records if x.get("mt_skipped")), "music_lines": music,
            "pass": not fails, "fails": fails}


def report(res: dict) -> str:
    keys = [k for k in res if k.startswith("lag_")]
    head = ", ".join(f"{k[4:-2]} s: {res[k]} s" for k in keys)
    verdict = "PASS" if res["pass"] else "FAIL: " + "; ".join(res["fails"])
    fmt = lambda v: "unavailable" if v is None else f"{v} s"  # noqa: E731
    shown = (f"; median shown after end of speech: source {fmt(res.get('src_shown_s'))}, "
             f"translation {fmt(res.get('mt_shown_s'))}")
    return (f"Live lag on a busy CPU (#1369): {head}{shown}; {res['lines']} lines, {res['skipped']} shown untranslated "
            f"to catch up; music-only lines: {len(res['music_lines'])}. {verdict}")


def build(data_dir, out, clip_s: float = CLIP_S) -> Path:
    import numpy as np
    import soundfile as sf

    sys.path.insert(0, str(ROOT))
    from tlwu_eval.runner import load_manifest, read_wav

    clips = [read_wav(r["wav"])[0] for r in load_manifest(data_dir) if r["lang"] == "en"]
    if not clips:
        raise SystemExit("no English clips in the manifest")
    gap = np.zeros(int(0.6 * SR), dtype="float32")
    t = np.arange(int(MUSIC_S * SR)) / SR
    chord = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63)) / 3
    music = (0.2 * chord * (1 + 0.3 * np.sin(2 * np.pi * 2 * t))).astype("float32")  # 2 Hz pulse, no speech
    parts, n, i, inserted = [], 0, 0, False
    while n < clip_s * SR:
        if not inserted and n >= MUSIC_AT_S * SR:
            parts.append(music)
            n += len(music)
            inserted = True
        c = clips[i % len(clips)].astype("float32")
        parts += [c, gap]
        n += len(c) + len(gap)
        i += 1
    audio = np.concatenate(parts)[: int(clip_s * SR)]
    sf.write(str(out), audio, SR)
    return Path(out)


def _spin():
    while True:
        pass


# eval.yml names engines the way tlwu_eval does (hymt2-cpu); the tinglongwu CLI only takes its own names (#1517).
# --device cpu is always passed, so hymt runs on the CPU.
CLI_ENGINE = {"hymt2": "hymt", "hymt2-cpu": "hymt", "hymt2-7b": "hymt7b"}


def cli_engine(name: str) -> str:
    return CLI_ENGINE.get(name, name)


def run(a) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        jsonl = Path(tmp) / "records.jsonl"
        cmd = [sys.executable, "-m", "tinglongwu", "--file", str(Path(a.audio).resolve()), "--to", a.to,
               "--device", "cpu", "--paced", "--latency-jsonl", str(jsonl)]
        if a.engine:
            cmd += ["--engine", cli_engine(a.engine)]
        if a.model:
            cmd += ["--model", a.model]
        if a.models_dir:
            cmd += ["--models-dir", str(Path(a.models_dir).resolve())]
        load = [multiprocessing.Process(target=_spin, daemon=True) for _ in range(a.busy)]
        for p in load:
            p.start()
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, timeout=a.clip + 600)
        finally:
            for p in load:
                p.terminate()
        if proc.returncode != 0 or not jsonl.is_file():
            print(f"tlwu failed (exit {proc.returncode}): {' '.join(cmd)}")
            return 2
        records = load_jsonl(jsonl)
    res = score(records, a.clip)
    res.update({"busy_cores": a.busy, "cmd": cmd})
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    print(report(res))
    return 0 if res["pass"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--data", required=True)
    b.add_argument("--out", default="livelag.wav")
    r = sub.add_parser("run")
    r.add_argument("--audio", required=True)
    r.add_argument("--to", default="zh")
    r.add_argument("--engine")
    r.add_argument("--model")
    r.add_argument("--models-dir")
    r.add_argument("--busy", type=int, default=1, help="background cores kept busy (default 1)")
    r.add_argument("--clip", type=float, default=CLIP_S)
    r.add_argument("--out", default="livelag.json")
    s = sub.add_parser("score")
    s.add_argument("--jsonl", required=True)
    s.add_argument("--clip", type=float, default=CLIP_S)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        print(build(a.data, a.out))
        return 0
    if a.cmd == "run":
        return run(a)
    res = score(load_jsonl(a.jsonl), a.clip)
    print(report(res))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
