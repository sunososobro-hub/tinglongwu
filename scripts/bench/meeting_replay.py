#!/usr/bin/env python
"""#1264: real-time meeting replay. One shared command for the field-test issues (#1252, #1253, #1255, #1256, #1259)
that verify with "replay a 30-min recording": feeds a long public multi-speaker recording to `tlwu --file --paced`
(1x speed, live clock, live ASR time budget) under a CPU cap and prints

    drops, audio coverage %, longest gap, lag p95, speaker-label count

    python scripts/bench/make_ci_smoke.py --pins eval/matrix_pins.json --out DATA          # pinned FLEURS clips
    python scripts/bench/meeting_replay.py build --data DATA --lang ru --minutes 30 --out REPLAY
    python scripts/bench/meeting_replay.py run --dir REPLAY --models-dir M --cpus 4 --speakers -- --to zh --engine hymt
    python scripts/bench/meeting_replay.py report --dir REPLAY                              # metrics again, no run

Recording: FLEURS test clips (CC-BY 4.0, eval/matrix_pins.json, sha256-checked by make_ci_smoke) of one language,
joined back to back and cycled to --minutes. Every FLEURS clip is a reader, so a speaker change happens at each
clip. Most joins are short (SHORT_GAP_S, below the 0.6 s VAD end-of-segment silence) so several clips run into one
long segment as in a dense meeting; every LONG_EVERY-th join is a real pause (LONG_GAP_S).
Metrics (written to REPLAY/metrics.json, plus one Markdown table on stdout):
  drops        sentences ASR dropped over its live time budget (`tlwu: asr: dropped ...` on stderr); cuts too
  coverage %   seconds of clip speech that some saved line's audio span ([start, end] in --record segments.jsonl)
               overlaps, over all clip speech seconds (whole clips, their silent lead-in / tail included)
  voiced cov % the same over the voiced parts of the clips only (#1252 Acceptance "every second of speech audio
               maps to some output line"): frame energy finds them at build time, so silence padding inside a
               FLEURS clip no longer counts as lost speech; `holes` lists the voiced stretches no line covers
  longest gap  longest stretch (wall seconds after playback start, inside the speech) with no new saved line
  lag p95      95th percentile of --latency-jsonl "total" (end of speech -> line shown), seconds
  labels       distinct speaker labels in the saved lines (--speakers), against the distinct clips played
  MT delay     (#1253) --latency-jsonl "mt" p50 / p95 (merge release -> line shown) and "mt_hold": how many lines
               had their translation held while ASR caught up, p95 / max seconds held (shown, not hidden)
  ASR p95      --latency-jsonl "asr" p95 (segment cut -> ASR done, queue wait included)
Nothing here judges PASS / FAIL: issues set their own limits on these numbers.

A/B (#1253 "dropped-segment count with MT on equals the ASR-only count"): run the same recording twice, once with
`-- --transcript-only` (ASR only) and once with MT, into two directories, then

    python scripts/bench/meeting_replay.py compare --asr-only DIR_A --with-mt DIR_B
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SR = 16000
SHORT_GAP_S = 0.25     # under the listen Segmenter's 0.6 s min_silence: neighbouring clips merge into one segment
LONG_GAP_S = 1.5       # a real pause: the segment ends
LONG_EVERY = 4         # every 4th join is a pause
DRAIN_S = 600.0        # after playback, tlwu may finish the ASR / MT backlog this long before it is stopped
READY_TIMEOUT_S = 900.0
READY_MARK = "* ready - listening"   # tinglongwu.cli.READY_LINE
DROP_MARK = "tlwu: asr: dropped a sentence"
CUT_MARK = "tlwu: asr: cut a sentence"
VOICED_FRAME_S = 0.02  # energy frame for voiced(): 20 ms
VOICED_REL_DB = -35.0  # a frame is voiced when its RMS is within 35 dB of the clip's loudest frames (p95)
VOICED_FLOOR = 1e-3    # ... and above this absolute RMS (digital silence / dither never counts)
VOICED_BRIDGE_S = 0.3  # voiced runs closer than this are one stretch (stops, short pauses inside a word)
HOLE_MIN_S = 0.5       # an uncovered voiced stretch shorter than this is not reported as a hole


# ---------------------------------------------------------------- pure parts (unit-tested)
def layout(durations: list[float], target_s: float) -> list[tuple[int, float, float]]:
    """(clip index, start s, end s) of clips cycled in order until the speech reaches target_s. Join k (after clip
    k, 1-based) is LONG_GAP_S when k % LONG_EVERY == 0, else SHORT_GAP_S."""
    if not durations:
        return []
    out, t, i = [], 0.0, 0
    while not out or out[-1][2] < target_s:
        k = i % len(durations)
        out.append((k, t, t + durations[k]))
        i += 1
        t += durations[k] + (LONG_GAP_S if i % LONG_EVERY == 0 else SHORT_GAP_S)
    return out


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if b > a):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def coverage(speech: list[tuple[float, float]], lines: list[tuple[float, float]]) -> float:
    """Share (0..1) of the speech seconds that some line's audio span overlaps."""
    sp, ln = _merge(speech), _merge(lines)
    total = sum(b - a for a, b in sp)
    if total <= 0:
        return 0.0
    hit, j = 0.0, 0
    for a, b in sp:
        while j < len(ln) and ln[j][1] <= a:
            j += 1
        k = j
        while k < len(ln) and ln[k][0] < b:
            hit += min(b, ln[k][1]) - max(a, ln[k][0])
            k += 1
    return hit / total


def voiced(audio, sr: int = SR) -> list[tuple[float, float]]:
    """(start s, end s) of the voiced stretches of one clip, by frame energy: frames within VOICED_REL_DB of the
    clip's p95 frame RMS (and above VOICED_FLOOR), runs closer than VOICED_BRIDGE_S merged."""
    import numpy as np

    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    n = int(sr * VOICED_FRAME_S)
    if n <= 0 or len(x) < n:
        return []
    fr = x[: len(x) // n * n].reshape(-1, n)
    rms = np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1))
    thr = max(VOICED_FLOOR, float(np.percentile(rms, 95)) * 10 ** (VOICED_REL_DB / 20))
    on = rms >= thr
    out: list[list[float]] = []
    for i in np.flatnonzero(on):
        a, b = i * VOICED_FRAME_S, (i + 1) * VOICED_FRAME_S
        if out and a - out[-1][1] <= VOICED_BRIDGE_S + 1e-9:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def holes(speech: list[tuple[float, float]], lines: list[tuple[float, float]],
          min_s: float = HOLE_MIN_S) -> list[tuple[float, float]]:
    """Stretches of speech (>= min_s) that no line's audio span overlaps, in time order."""
    ln = _merge(lines)
    out = []
    for a, b in _merge(speech):
        cur = a
        for c, d in ln:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append((cur, c))
            cur = max(cur, d)
        if cur < b:
            out.append((cur, b))
    return [(round(a, 2), round(b, 2)) for a, b in out if b - a >= min_s]


def longest_gap(line_times: list[float], start: float, end: float) -> tuple[float, float, float]:
    """(seconds, from, to): the longest stretch of [start, end] with no line time inside (start / end count as
    edges, so no line at all gives the whole span)."""
    pts = sorted(x for x in line_times if start <= x <= end)
    edges = [start, *pts, end]
    a, b = max(zip(edges, edges[1:]), key=lambda ab: ab[1] - ab[0])
    return b - a, a, b


def p95(values: list[float]) -> float | None:
    """Nearest-rank 95th percentile; None for no values."""
    xs = sorted(values)
    if not xs:
        return None
    import math

    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


def count_events(log_lines: list[str]) -> tuple[int, int]:
    """(drops, cuts) from tlwu's stderr lines."""
    drops = sum(DROP_MARK in x for x in log_lines)
    cuts = sum(CUT_MARK in x for x in log_lines)
    return drops, cuts


def mt_delay(lat: list[dict]) -> dict:
    """#1253: MT delay from --latency-jsonl rows: "mt" p50 / p95, lines whose MT was held for ASR ("mt_hold" > 0)
    and the p95 / max held seconds, "asr" p95. None where no row has the field (ASR-only runs have no "mt_hold")."""
    def nums(key):
        import math

        return [float(r[key]) for r in lat if isinstance(r.get(key), (int, float)) and not isinstance(r[key], bool)
                and math.isfinite(r[key])]  # Infinity / NaN would end up as non-standard JSON in metrics.json

    def rnd(v, n=2):
        return None if v is None else round(v, n)

    mt, hold, asr = nums("mt"), nums("mt_hold"), nums("asr")
    held = [h for h in hold if h > 0]
    return {"mt_p50_s": rnd(_pct(mt, 0.50)), "mt_p95_s": rnd(_pct(mt, 0.95)), "asr_p95_s": rnd(_pct(asr, 0.95)),
            "mt_held_lines": len(held) if hold else None, "mt_hold_p95_s": rnd(_pct(held, 0.95)) if hold else None,
            "mt_hold_max_s": rnd(max(held)) if held else (0.0 if hold else None)}


def _pct(values: list[float], q: float) -> float | None:
    """Nearest-rank q-quantile; None for no values."""
    import math

    xs = sorted(values)
    return xs[max(0, math.ceil(q * len(xs)) - 1)] if xs else None


def label_count(rows: list[dict]) -> int:
    return len({r["speaker"] for r in rows if r.get("speaker")})


def wall_offsets(rows: list[dict], t0: float) -> list[float]:
    """Seconds after epoch t0 at which each saved line was written (--save "wall", ISO 8601, 1 s resolution)."""
    out = []
    for r in rows:
        try:
            out.append(datetime.datetime.fromisoformat(r["wall"]).timestamp() - t0)
        except (KeyError, TypeError, ValueError):
            pass
    return out


def metrics(meta: dict, log_lines: list[str], saved: list[dict], segs: list[dict], lat: list[dict],
            t0: float | None) -> dict:
    """Every number the replay prints, from the run's outputs (pure: no files, no clock)."""
    drops, cuts = count_events(log_lines)
    speech = [tuple(s) for s in meta["spans"]]
    lines = [(float(s["start"]), float(s["end"])) for s in segs if "start" in s and "end" in s]
    s0, s1 = meta["speech"]
    gap = longest_gap(wall_offsets(saved, t0), s0, s1) if t0 is not None else None
    lag = p95([float(r["total"]) for r in lat if isinstance(r.get("total"), (int, float))])
    vs = [tuple(s) for s in meta.get("voiced") or []]
    hl = holes(vs, lines) if vs else []
    return {"voiced_coverage_pct": round(100 * coverage(vs, lines), 1) if vs else None,
            "holes": len(hl), "holes_s": round(sum(b - a for a, b in hl), 1),
            "holes_top": [list(h) for h in sorted(hl, key=lambda h: h[0] - h[1])[:5]],
            "voiced_s": round(sum(b - a for a, b in _merge(vs)), 1) if vs else None,
            "drops": drops, "cuts": cuts, "coverage_pct": round(100 * coverage(speech, lines), 1),
            "longest_gap_s": round(gap[0], 1) if gap else None,
            "longest_gap_at": [round(gap[1]), round(gap[2])] if gap else None,
            "lag_p95_s": round(lag, 2) if lag is not None else None, "lines": len(saved),
            "labels": label_count(saved), "clips_distinct": len(set(meta.get("clips") or [])),
            "speech_s": round(sum(b - a for a, b in _merge(speech)), 1), "seconds": meta.get("seconds"),
            **mt_delay(lat)}


def table(m: dict, title: str = "meeting replay (#1264)") -> str:
    def f(v, unit=""):
        return "n/a" if v is None else f"{v}{unit}"

    gap = f(m.get("longest_gap_s"), " s") + (f" (at {m['longest_gap_at'][0]}-{m['longest_gap_at'][1]} s)"
                                               if m.get("longest_gap_at") else "")
    top = ", ".join(f"{a:.1f}-{b:.1f} s" for a, b in m.get("holes_top") or [])
    rows = [("drops", f(m["drops"]) + f" (cuts {m['cuts']})"), ("audio coverage", f(m["coverage_pct"], " %")),
            ("voiced coverage", f(m.get("voiced_coverage_pct"), " %")
             + (f" ({m.get('voiced_s')} s voiced)" if m.get("voiced_s") is not None else "")),
            ("holes (voiced, no line)", f"{m.get('holes', 0)} = {m.get('holes_s', 0.0)} s"
             + (f"; longest: {top}" if top else "")),
            ("longest gap", gap), ("lag p95", f(m.get("lag_p95_s"), " s")),
            ("speaker labels", f"{m['labels']} (clips played: {m['clips_distinct']} distinct)"),
            ("ASR p95 (cut -> text)", f(m.get("asr_p95_s"), " s")),
            ("MT delay p50 / p95", f"{f(m.get('mt_p50_s'), ' s')} / {f(m.get('mt_p95_s'), ' s')}"),
            ("MT held for ASR", f"{f(m.get('mt_held_lines'))} line(s), p95 {f(m.get('mt_hold_p95_s'), ' s')}, "
                                f"max {f(m.get('mt_hold_max_s'), ' s')}"),
            ("lines", f(m["lines"])), ("speech / recording", f"{m['speech_s']} s / {m['seconds']} s")]
    head = f"### {title}\n\n" + (f"{m['setup']}\n\n" if m.get("setup") else "")
    return head + "| metric | value |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


def summary_line(m: dict) -> str:
    def f(v):
        return "n/a" if v is None else v

    return (f"replay: drops={m['drops']} cuts={m['cuts']} coverage={m['coverage_pct']}% "
            f"voiced_coverage={f(m.get('voiced_coverage_pct'))}% holes={m.get('holes', 0)}/{m.get('holes_s', 0.0)}s "
            f"longest_gap={f(m.get('longest_gap_s'))}s lag_p95={f(m.get('lag_p95_s'))}s labels={m['labels']} "
            f"lines={m['lines']}")


AB_ROWS = [("drops", "drops"), ("cuts", "cuts"), ("voiced coverage %", "voiced_coverage_pct"),
           ("holes (voiced, no line)", "holes"), ("holes s", "holes_s"), ("audio coverage %", "coverage_pct"),
           ("ASR p95 s", "asr_p95_s"), ("lag p95 s", "lag_p95_s"), ("longest gap s", "longest_gap_s"),
           ("MT delay p50 s", "mt_p50_s"), ("MT delay p95 s", "mt_p95_s"), ("MT held lines", "mt_held_lines"),
           ("MT hold p95 s", "mt_hold_p95_s"), ("MT hold max s", "mt_hold_max_s"), ("lines", "lines")]


def compare(a: dict, b: dict) -> dict:
    """#1253 A/B of two replays of the same recording: a = ASR only (--transcript-only), b = ASR + MT. Returns the
    rows (metric, a, b, b - a) and the drop / cut deltas. Judges nothing; the issue's limit is drops_delta == 0."""
    def d(x, y):
        return round(y - x, 2) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None

    ran = bool(a.get("completed")) and bool(b.get("completed"))  # both tlwu runs played the whole recording
    rows = [(name, a.get(k), b.get(k), d(a.get(k), b.get(k)) if ran else None) for name, k in AB_ROWS]
    same_len = a.get("seconds") == b.get("seconds") and a.get("speech_s") == b.get("speech_s")
    same_id = a.get("recording") is not None and a.get("recording") == b.get("recording")
    return {"rows": rows, "drops_asr_only": a.get("drops"), "drops_with_mt": b.get("drops"),
            "drops_delta": d(a.get("drops"), b.get("drops")) if ran else None,
            "cuts_delta": d(a.get("cuts"), b.get("cuts")) if ran else None, "both_ran": ran,
            "same_recording": same_id and same_len, "same_duration": same_len}


def compare_table(c: dict, setup_a: str = "", setup_b: str = "") -> str:
    def f(v):
        return "n/a" if v is None else (f"+{v}" if isinstance(v, (int, float)) and v > 0 else f"{v}")

    head = "### meeting replay A/B: ASR only vs ASR + MT (#1253)\n\n"
    if setup_a or setup_b:
        head += f"A (ASR only): {setup_a or 'n/a'}  \nB (ASR + MT): {setup_b or 'n/a'}\n\n"
    if not c.get("both_ran", True):
        head += "**warning: a replay did not run to the end (not ready, or tlwu exited early): no deltas**\n\n"
    if not c["same_recording"]:
        head += "**warning: A and B did not replay the same recording**\n\n"
    def cell(v):
        return "n/a" if v is None else v

    body = "\n".join(f"| {n} | {cell(x)} | {cell(y)} | {f(z)} |"
                      for n, x, y, z in c["rows"])
    return head + "| metric | A: ASR only | B: ASR + MT | B - A |\n|---|---|---|---|\n" + body + "\n"


def compare_line(c: dict) -> str:
    def f(v):
        return "n/a" if v is None else v

    return (f"replay-ab: drops asr_only={f(c['drops_asr_only'])} with_mt={f(c['drops_with_mt'])} "
            f"delta={f(c['drops_delta'])} cuts_delta={f(c['cuts_delta'])}")


def tlwu_cmd(rdir: Path, meta: dict, mdir: str | None, speakers: bool, extra: list[str]) -> list[str]:
    """The tlwu command line of one replay: paced file, live clock, every output the metrics need."""
    cmd = [sys.executable, "-m", "tinglongwu", "--file", str(rdir / meta["wav"]), "--paced", "--device", "cpu",
           "--save", str(rdir / "saved.jsonl"), "--latency-jsonl", str(rdir / "latency.jsonl"),
           "--record", str(rdir / "rec")]
    if "--lang" not in extra:
        cmd += ["--lang", meta["lang"]]
    if mdir:
        cmd += ["--models-dir", mdir]
    if speakers:
        cmd.append("--speakers")
    return cmd + list(extra)


def cpu_set(n: int, have: list[int]) -> list[int]:
    """The first n CPUs this process may run on (all of them when n <= 0 or n >= what is there)."""
    have = sorted(have)
    return have if n <= 0 or n >= len(have) else have[:n]


# ---------------------------------------------------------------- build / run (CI)
def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for x in path.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(x))
        except ValueError:
            pass
    return out


RUN_OUTPUTS = ("saved.jsonl", "latency.jsonl", "tlwu.log", "t0.txt", "done.json", "metrics.json")


def clear_run(rdir: Path) -> None:
    """Remove the outputs of an earlier run in rdir (a rerun or a rebuilt recording never reports stale lines)."""
    for name in RUN_OUTPUTS:
        (rdir / name).unlink(missing_ok=True)
    (rdir / "rec" / "segments.jsonl").unlink(missing_ok=True)  # the Recorder appends


def build(data: Path, lang: str, minutes: float, out: Path) -> dict:
    import numpy as np
    import soundfile as sf

    rows = [json.loads(x) for x in (data / "manifest.jsonl").read_text("utf-8").splitlines() if x.strip()]
    sel = [r for r in rows if r["lang"] == lang]
    if not sel:
        raise SystemExit(f"no '{lang}' clips in {data / 'manifest.jsonl'}")
    audio = [sf.read(str(data / r["wav"]), dtype="float32", always_2d=True)[0].mean(axis=1) for r in sel]
    lay = layout([len(a) / SR for a in audio], minutes * 60)
    vo = [voiced(a) for a in audio]
    x = np.zeros(int((lay[-1][2] + LONG_GAP_S) * SR), np.float32)
    for k, s, _e in lay:
        x[int(s * SR):int(s * SR) + len(audio[k])] = audio[k]
    out.mkdir(parents=True, exist_ok=True)
    clear_run(out)  # outputs of a run of the previous recording must not be labelled as this one
    sf.write(str(out / "replay.wav"), x, SR)
    meta = {"wav": "replay.wav", "lang": lang, "seconds": round(len(x) / SR, 2),
            "speech": [round(lay[0][1], 2), round(lay[-1][2], 2)],
            "spans": [[round(s, 2), round(e, 2)] for _k, s, e in lay], "clips": [sel[k]["id"] for k, _s, _e in lay],
            "voiced": [[round(s + a, 2), round(s + b, 2)] for k, s, _e in lay for a, b in vo[k]],
            "source": "FLEURS test (CC-BY 4.0), eval/matrix_pins.json"}
    (out / "replay.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    print(f"replay: {out / 'replay.wav'}: {meta['seconds']:.0f} s, {len(lay)} clips "
          f"({len(set(meta['clips']))} distinct, {lang})")
    return meta


def run(rdir: Path, mdir: str | None, cpus: int, speakers: bool, extra: list[str], drain: float = DRAIN_S) -> dict:
    meta = json.loads((rdir / "replay.json").read_text("utf-8"))
    clear_run(rdir)
    cmd = tlwu_cmd(rdir, meta, mdir, speakers, extra)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1", TLWU_NO_UPDATE="1")
    pre = None
    if hasattr(os, "sched_setaffinity"):
        pin = cpu_set(cpus, list(os.sched_getaffinity(0)))
        pre = (lambda: os.sched_setaffinity(0, pin))  # noqa: E731 - runs in the child before exec
        print(f"replay: CPUs {pin} of {os.cpu_count()}", flush=True)
    print("replay: " + " ".join(cmd), flush=True)
    ready, t0 = threading.Event(), [None]
    stopped = [False]  # the harness stopped tlwu (drain deadline / readiness timeout)
    log_lines: list[str] = []
    with open(rdir / "tlwu.log", "w", encoding="utf-8") as log:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=env, cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", preexec_fn=pre)

        def pump():
            for line in p.stdout:
                log.write(line)
                log.flush()
                log_lines.append(line.rstrip("\n"))
                if not ready.is_set() and READY_MARK in line:
                    t0[0] = time.time()  # PacedSource starts its clock right after this line
                    ready.set()

        th = threading.Thread(target=pump, daemon=True)
        th.start()
        try:
            deadline = time.monotonic() + READY_TIMEOUT_S
            while not ready.wait(1.0):
                if p.poll() is not None or time.monotonic() > deadline:
                    break
            if ready.is_set():
                (rdir / "t0.txt").write_text(f"{t0[0]:.3f}\n", "utf-8")
                end = time.monotonic() + meta["seconds"] + drain
                while p.poll() is None and time.monotonic() < end:  # the file ends -> tlwu flushes and exits
                    time.sleep(1.0)
        finally:
            if p.poll() is None:
                stopped[0] = True
                p.terminate()
                try:
                    p.wait(30)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            th.join(10)
    if not ready.is_set():
        tail = "\n".join(log_lines[-15:])
        raise SystemExit(f"tlwu did not get ready (exit {p.returncode}); last lines:\n{tail}")
    # playback reached its end: tlwu exited 0 at the end of the file, or the harness stopped it after the drain
    (rdir / "done.json").write_text(json.dumps({"completed": bool(stopped[0] or p.returncode == 0),
                                                "exit": p.returncode, "recording": recording_id(meta)}), "utf-8")
    m = report(rdir)
    if not stopped[0] and p.returncode != 0:  # tlwu failed on its own; the partial metrics are printed above
        tail = "\n".join(log_lines[-15:])
        raise SystemExit(f"tlwu exited with {p.returncode} during the replay; last lines:\n{tail}")
    return m


def recording_id(meta: dict) -> str:
    """Short fingerprint of the replayed recording (clip order + layout + length), so an A/B can tell two runs of
    the same recording from two recordings that only happen to have the same length."""
    import hashlib

    key = json.dumps([meta.get("lang"), meta.get("clips"), meta.get("spans"), meta.get("seconds")], sort_keys=True)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def report(rdir: Path, quiet: bool = False) -> dict:
    meta = json.loads((rdir / "replay.json").read_text("utf-8"))
    log = (rdir / "tlwu.log").read_text("utf-8", errors="replace").splitlines() if (rdir / "tlwu.log").is_file() else []
    t0f = rdir / "t0.txt"
    t0 = float(t0f.read_text("utf-8").strip()) if t0f.is_file() else None
    m = metrics(meta, log, read_jsonl(rdir / "saved.jsonl"), read_jsonl(rdir / "rec" / "segments.jsonl"),
                read_jsonl(rdir / "latency.jsonl"), t0)
    plan = next((x.split("tlwu: ", 1)[1] for x in log if "CPU mode threads:" in x), "")
    done = {}
    if (rdir / "done.json").is_file():
        try:
            done = json.loads((rdir / "done.json").read_text("utf-8"))
        except ValueError:
            done = {}
    m["ran"] = t0 is not None  # tlwu got ready and playback started (run() writes t0.txt only then)
    m["completed"] = bool(done.get("completed")) if m["ran"] else False  # no done.json: tlwu died mid-run
    m["recording"] = done.get("recording") or recording_id(meta)  # the recording the run actually played
    m["setup"] = f"{meta['lang']}, {meta['seconds'] / 60:.0f} min at 1x" + (f", {plan}" if plan else "")
    (rdir / "metrics.json").write_text(json.dumps(m, indent=1), "utf-8")
    if not quiet:
        print(table(m))
        print(summary_line(m), flush=True)
    return m


def compare_dirs(asr_only: Path, with_mt: Path, out: Path | None = None) -> dict:
    """compare(): metrics of both run dirs (recomputed from their outputs), one table + one line on stdout."""
    a, b = report(asr_only, quiet=True), report(with_mt, quiet=True)
    c = compare(a, b)
    if out is not None:
        out.write_text(json.dumps(c, indent=1), "utf-8")
    print(compare_table(c, a.get("setup", ""), b.get("setup", "")))
    print(compare_line(c), flush=True)
    return c


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="join pinned FLEURS clips into one long multi-speaker recording")
    b.add_argument("--data", required=True, type=Path, help="make_ci_smoke.py output (manifest.jsonl + wavs)")
    b.add_argument("--lang", default="ru")
    b.add_argument("--minutes", type=float, default=30.0)
    b.add_argument("--out", required=True, type=Path)
    r = sub.add_parser("run", help="replay at 1x through tlwu and print the metrics (tlwu args after --)")
    r.add_argument("--dir", required=True, type=Path)
    r.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS"))
    r.add_argument("--cpus", type=int, default=4, help="pin tlwu to this many CPUs (Linux; 0 = all)")
    r.add_argument("--speakers", action="store_true", help="tlwu --speakers (needs the speaker model)")
    r.add_argument("--drain", type=float, default=DRAIN_S,
                   help="seconds tlwu may keep working on its backlog after playback (then it is stopped)")
    p = sub.add_parser("report", help="print the metrics of the last run in --dir")
    p.add_argument("--dir", required=True, type=Path)
    c = sub.add_parser("compare", help="#1253 A/B: metrics of an ASR-only run next to an ASR + MT run")
    c.add_argument("--asr-only", required=True, type=Path, help="run dir of the --transcript-only replay")
    c.add_argument("--with-mt", required=True, type=Path, help="run dir of the replay with translation")
    c.add_argument("--out", type=Path, help="write the comparison as JSON here")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.data, a.lang, a.minutes, a.out)
    elif a.cmd == "run":
        run(a.dir, a.models_dir, a.cpus, a.speakers, extra, a.drain)
    elif a.cmd == "compare":
        compare_dirs(a.asr_only, a.with_mt, a.out)
    else:
        report(a.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
