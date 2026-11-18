#!/usr/bin/env python
"""#1194: three owner hand-tests on the owner-PC hw-tests runner, with recorded speech instead of a person.

    python scripts/bench/make_ci_smoke.py --pins eval/matrix_pins.json --out DATA     # pinned FLEURS clips (sha256)
    python scripts/hw_live_checks.py clips --data DATA --out CLIPS
    python scripts/hw_live_checks.py file --clips CLIPS --models-dir M [-- ARGS]  #1247 file half of #894, per clip
    python scripts/hw_live_checks.py mic --clips CLIPS --models-dir M       #894 live mic vs file, subtitle gaps
    python scripts/hw_live_checks.py meeting --clips CLIPS --models-dir M   #4 meeting mode: ◀ Them only for system audio
    python scripts/hw_live_checks.py loopback --clips CLIPS --models-dir M  #348 another process's audio via loopback

Clips: FLEURS test (CC-BY 4.0, eval/matrix_pins.json, every WAV checked against its pinned sha256 by make_ci_smoke)
joined with GAP_S of silence; each FLEURS clip is a different reader, so the joined clip has many speakers.
  mic      : en clips cycled to MIC_S seconds, played into "CABLE Input"; `verify_offline.py --mic` runs tlwu on
             "CABLE Output" (the microphone path). PASS when WER(mic) <= MAX_WER_RATIO x WER(same clip as --file)
             and no MAX_GAP_S stretch of the clip passes without a subtitle.
  meeting  : clip A (zh) into "CABLE Input", the speak side's --mic "CABLE Output" (▶ Me); clip B (en) played by a
             separate process on the real speakers, which the listen side (--listen --capture-device) hears through
             WASAPI loopback (◀ Them). PASS when >= MIN_LABEL_OK of the saved lines carry the side of the clip
             their text comes from, with >= 1 line from each clip.
  loopback : a separate process (stand-in for Discord) plays en speech on the real speakers; tlwu with default
             loopback capture must save >= 1 line whose words come from that speech.
Each check prints one `PASS ...` / `FAIL ...` line (the hw-tests step summary shows it) and exits 0 / 1.
The speakers are audible on the owner PC during meeting / loopback (about LOOP_S seconds each).
"""
from __future__ import annotations

import argparse
import datetime
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SR, PLAY_SR = 16000, 48000
GAP_S = 1.0           # silence between two FLEURS clips (a speaker change)
MIC_S = 300.0         # #894: "a 5-minute recorded multi-speaker clip"
MEET_S = 60.0         # #4: length of clip A and of clip B
LOOP_S = 30.0         # #348
MAX_WER_RATIO = 1.25  # #894 Acceptance (1)
MAX_GAP_S = 10.0      # #894 Acceptance (1)
MIN_LABEL_OK = 0.90   # #4 Acceptance (2)
MIN_WORD_HIT = 0.5    # a line "comes from" a clip when >= half of its tokens occur in that clip's reference
TAIL_S = 15.0         # keep listening after playback: the last sentence waits up to --max-wait
READY_TIMEOUT_S = 600.0
CABLE_IN, CABLE_OUT = "CABLE Input", "CABLE Output"
READY_MARK = "* ready - listening"   # tinglongwu.cli.READY_LINE (listen side, stderr)
SPEAK_READY_MARK = "listening... speak"  # tinglongwu.reverse.proto, speak side live


# ---------------------------------------------------------------- pure parts (tested on Linux)
def layout(durations: list[float], target_s: float, gap_s: float = GAP_S, start: int = 0) -> list[tuple[int, float, float]]:
    """(clip index, start s, end s) of clips joined with gap_s, cycling from `start` until the speech reaches
    target_s."""
    out, t, i = [], 0.0, start
    while not out or out[-1][2] < target_s:
        k = i % len(durations)
        out.append((k, t, t + durations[k]))
        t += durations[k] + gap_s
        i += 1
        if i - start > 10 * len(durations):
            break
    return out


def max_gap(line_times: list[float], speech_start: float, speech_end: float) -> float:
    """Longest stretch inside [speech_start, speech_end] (seconds of the clip) with no subtitle shown."""
    pts = sorted(x for x in line_times if speech_start <= x <= speech_end)
    edges = [speech_start, *pts, speech_end]
    return max(b - a for a, b in zip(edges, edges[1:]))


def gap_span(line_times: list[float], speech_start: float, speech_end: float) -> tuple[float, float]:
    """(start, end) in clip seconds of the longest stretch max_gap() measures (#1247: where the subtitles stopped)."""
    pts = sorted(x for x in line_times if speech_start <= x <= speech_end)
    edges = [speech_start, *pts, speech_end]
    return max(zip(edges, edges[1:]), key=lambda ab: ab[1] - ab[0])


def clips_in(spans: list[list[float]], a: float, b: float) -> list[int]:
    """Indices of the clips whose speech overlaps [a, b] seconds of the joined clip."""
    return [i for i, (s, e) in enumerate(spans) if s < b and e > a]


MIN_CLIP_HIT = 0.5  # #1247: a clip is "heard" when >= half of its distinct words of >= 4 letters occur in the output


def missed_clips(rows: list[dict], refs: list[str], lang: str = "en") -> list[int]:
    """#1247: indices of clips whose words are mostly absent from the saved lines. Tells a clip tlwu never output
    (missing in --file too = recognizer / segmenter) apart from one lost on the CABLE path (missing in --mic only)."""
    from tlwu_eval.scoring import tokenize

    heard = set(tokenize(" ".join(r.get("src", "") for r in rows), lang))
    out = []
    for i, ref in enumerate(refs):
        words = {w for w in tokenize(ref, lang) if len(w) >= 4}
        if words and sum(w in heard for w in words) / len(words) < MIN_CLIP_HIT:
            out.append(i)
    return out


def diagnose_mic(meta: dict, file_rows: list[dict], mic_rows: list[dict], offsets: list[float]) -> str:
    """#1247: one line for the step summary: file vs mic line counts, missed clips, where the longest gap is."""
    refs, spans = meta.get("refs") or [], meta.get("spans") or []
    a, b = gap_span(offsets, *meta["speech"])
    parts = [f"file lines {len(file_rows)}, mic lines {len(mic_rows)}"]
    if refs:
        mf, mm = missed_clips(file_rows, refs), missed_clips(mic_rows, refs)
        parts.append(f"clips missed: file {len(mf)}/{len(refs)} {mf[:12]}, mic {len(mm)}/{len(refs)} {mm[:12]}")
    gap_clips = clips_in(spans, a, b) if spans else []
    parts.append(f"longest gap {a:.0f}-{b:.0f} s" + (f" (clips {gap_clips[0]}..{gap_clips[-1]})" if gap_clips else ""))
    return "; ".join(parts)


def gap_context(offsets: list[float], srcs: list[str], a: float, b: float, n: int = 2) -> str:
    """#1247: the saved lines just before and after the longest gap [a, b] (clip seconds), as "time: text" pairs,
    so the job log shows what the subtitles did there (the hw-live-checks artifact is not readable everywhere)."""
    rows = sorted(zip(offsets, srcs))
    before = [r for r in rows if r[0] <= a][-n:]
    after = [r for r in rows if r[0] >= b][:n]
    def fmt(r):
        return f"{r[0]:.1f}s {r[1][:60]!r}"
    return "before: " + "; ".join(fmt(r) for r in before) + " | after: " + "; ".join(fmt(r) for r in after)


def match_latency(srcs: list[str], lat_rows: list[dict]) -> list[dict | None]:
    """#1247: the --latency-jsonl record of each saved line (None when there is none). The live run writes one record
    per final line in the same order as --save; when the counts differ, each line takes the next unused record with
    the same text (only text that occurs once on both sides)."""
    if len(lat_rows) == len(srcs) and all(r.get("text") == s for r, s in zip(lat_rows, srcs)):
        return list(lat_rows)
    # a text said more than once (e.g. "Yes.") cannot tell its records apart: those lines stay unmatched
    from collections import Counter

    n_src, n_lat = Counter(srcs), Counter(r.get("text") for r in lat_rows)
    by_text = {r.get("text"): r for r in lat_rows if n_lat[r.get("text")] == 1}
    return [by_text.get(s) if n_src[s] == 1 else None for s in srcs]


def _secs(v) -> str:
    return f"{v:.1f}s" if isinstance(v, (int, float)) and not isinstance(v, bool) else "?"


def latency_context(offsets: list[float], srcs: list[str], lat: list[dict | None], a: float, b: float,
                    n: int = 3) -> list[str]:
    """#1247: for the saved lines around the longest gap [a, b], when each was shown (clip s), when its speech ended
    (shown minus --latency-jsonl "total"), the cut / ASR seconds and the whole text. A line whose speech ended long
    before it was shown means the gap is processing delay; a speech end late in the gap means no line was cut for
    the speech before it (segmenter / ASR output)."""
    rows = sorted(zip(offsets, srcs, lat), key=lambda r: r[0])
    near = [r for r in rows if r[0] <= a][-n:] + [r for r in rows if r[0] >= b][:n]
    out = []
    for off, src, rec in near:
        if rec is None or not isinstance(rec.get("total"), (int, float)):
            out.append(f"shown {off:.1f}s (no latency record) {src[:120]!r}")
            continue
        out.append(f"shown {off:.1f}s speech end {off - rec['total']:.1f}s vad {_secs(rec.get('vad'))} "
                   f"asr {_secs(rec.get('asr'))} {src[:120]!r}")
    return out


def latency_summary(lat_rows: list[dict]) -> str:
    """#1247: one line over every live line: median / max seconds from speech end to shown, and the slowest ASR call."""
    tot = sorted(r["total"] for r in lat_rows if isinstance(r.get("total"), (int, float)))
    asr = [r["asr"] for r in lat_rows if isinstance(r.get("asr"), (int, float))]
    if not tot:
        return "no latency records"
    return (f"{len(tot)} lines, speech end -> shown median {statistics.median(tot):.1f} s, max {tot[-1]:.1f} s; "
            f"ASR max {f'{max(asr):.1f} s' if asr else '?'}")


def tlwu_notes(log_lines: list[str], limit: int = 20) -> list[str]:
    """#1247: the live run's own `tlwu:` status lines (speech model, warm-up, asr cut/drop, model switch, catch-up),
    first `limit` of them, for the job log."""
    return [ln.rstrip() for ln in log_lines if ln.startswith("tlwu:")][:limit]


def clip_lines(spans: list[list[float]], refs: list[str], rec: list[dict], lang: str = "en") -> list[dict]:
    """#1247: per clip of the joined recording, which saved lines' audio ([start, end] in --record segments.jsonl)
    overlaps it and how many of its words (>= 4 letters) those lines hold. A clip with no line at all was never
    output (segmenter / silence / hallucination filter); a clip with lines but a low hit share was mis-heard."""
    from tlwu_eval.scoring import tokenize

    out = []
    for i, ((s, e), ref) in enumerate(zip(spans, refs)):
        lines = [r for r in rec if r.get("start", 0.0) < e and r.get("end", 0.0) > s]
        heard = set(tokenize(" ".join(r.get("source", "") for r in lines), lang))
        words = {w for w in tokenize(ref, lang) if len(w) >= 4}
        hit = sum(w in heard for w in words) / len(words) if words else 1.0
        out.append({"clip": i, "span": [s, e], "lines": len(lines), "hit": round(hit, 2),
                    "src": [r.get("source", "") for r in lines], "ref": ref})
    for r in out:  # a line that holds ANOTHER clip's words: the clip's reference does not belong to its audio
        got = set(tokenize(" ".join(r["src"]), lang))
        # refs repeat (the clip list cycles): on a tie the clip itself wins, so a heard clip never names its twin
        best = max(((sum(w in got for w in ws) / len(ws), j == r["clip"], j) for j, ws in
                    ((j, {w for w in tokenize(x, lang) if len(w) >= 4}) for j, x in enumerate(refs)) if ws),
                   default=(0.0, False, None))
        r["best_ref"] = best[2] if best[0] >= MIN_CLIP_HIT and best[2] != r["clip"] else None
    return out


def add_levels(rows: list[dict], audio, sr: int = SR) -> None:
    """#1247: RMS of each clip's audio in dBFS, so a clip with no line can be told from a near-silent recording."""
    import numpy as np

    for r in rows:
        x = audio[int(r["span"][0] * sr):int(r["span"][1] * sr)]
        rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0
        r["rms_dbfs"] = round(20 * np.log10(max(rms, 1e-6)), 1)


def clip_table(rows: list[dict]) -> str:
    """#1247: one line per clip that is missed (hit < MIN_CLIP_HIT): where, how many lines, what tlwu wrote."""
    bad = [r for r in rows if r["hit"] < MIN_CLIP_HIT]
    head = f"clips missed {len(bad)}/{len(rows)} (no line: {sum(not r['lines'] for r in bad)})"
    if all("rms_dbfs" in r for r in rows):  # #1247: are the missed clips the quiet ones?
        def med(xs):
            xs = sorted(xs)
            return f"{xs[len(xs) // 2]:.1f}" if xs else "n/a"
        good = [r["rms_dbfs"] for r in rows if r["hit"] >= MIN_CLIP_HIT]
        lost = [r["rms_dbfs"] for r in bad]
        head += (f"; rms dBFS median heard {med(good)} (min {min(good, default=float('nan')):.1f}),"
                 f" missed {med(lost)} (max {max(lost, default=float('nan')):.1f})")
    body = [f"  clip {r['clip']} {r['span'][0]:.0f}-{r['span'][1]:.0f} s lines={r['lines']} hit={r['hit']:.2f}"
            + (f" rms={r['rms_dbfs']:.1f}dBFS" if "rms_dbfs" in r else "")
            + (f" got-is-clip={r['best_ref']}" if r.get("best_ref") is not None else "")
            + f" ref={r['ref'][:60]!r} got={' | '.join(r['src'])[:120]!r}" for r in bad]
    return "\n".join([head, *body])


def wall_offsets(rows: list[dict], t0: float) -> list[float]:
    """Seconds after epoch t0 at which each saved line (--save "wall", ISO 8601) was written.

    #1247: "wall" has whole-second resolution (sink.JsonlSaveSink), so a gap between two lines read from it is off by
    up to 1 s; next to a 10 s limit that turned a 10.1 s gap into "11.0 s". Every line also has "t" (seconds since
    the save file was opened, 0.01 s resolution). floor(start + t) == wall for every line bounds the epoch `start` of
    that clock to [max(wall - t), min(wall - t) + 1); its midpoint plus "t" gives each line's time to within half
    the width of that interval. Rows without a numeric "t", or bounds that do not overlap, fall back to "wall"."""
    walls = [(datetime.datetime.fromisoformat(r["wall"]).timestamp(), r.get("t")) for r in rows if r.get("wall")]
    ts = [t for _, t in walls if isinstance(t, (int, float)) and not isinstance(t, bool)]
    if walls and len(ts) == len(walls):
        lo = max(w - t for w, t in walls)
        hi = min(w - t for w, t in walls) + 1.0
        if lo < hi:
            start = (lo + hi) / 2
            return [start + t - t0 for _, t in walls]
    return [w - t0 for w, _ in walls]


def check_mic(file_wer: float, mic_wer: float, gap: float, n_lines: int) -> tuple[bool, str]:
    reasons = []
    if n_lines == 0:
        reasons.append("no line from the microphone")
    if mic_wer > MAX_WER_RATIO * file_wer:
        reasons.append(f"mic WER > {MAX_WER_RATIO}x file WER")
    if gap > MAX_GAP_S:
        reasons.append(f"gap without a subtitle {gap:.1f} s > {MAX_GAP_S:.0f} s")
    ratio = mic_wer / file_wer if file_wer else float("inf") if mic_wer else 1.0
    msg = (f"WER mic {mic_wer:.3f} vs file {file_wer:.3f} (x{ratio:.2f}, limit {MAX_WER_RATIO}), "
           f"longest gap {gap:.1f} s (limit {MAX_GAP_S:.0f}), {n_lines} mic lines")
    return not reasons, msg + ("; " + "; ".join(reasons) if reasons else "")


def _hit(text: str, ref: str, lang: str) -> float:
    from tlwu_eval.scoring import tokenize

    toks, vocab = tokenize(text, lang), set(tokenize(ref, lang))
    return sum(t in vocab for t in toks) / len(toks) if toks else 0.0


def origin(text: str, ref_a: str, ref_b: str) -> str | None:
    """'a' (zh clip A), 'b' (en clip B) or None: which reference the line's tokens come from."""
    ha, hb = _hit(text, ref_a, "zh"), _hit(text, ref_b, "en")
    if max(ha, hb) < MIN_WORD_HIT:
        return None
    return "a" if ha > hb else "b"


def check_meeting(rows: list[dict], ref_a: str, ref_b: str) -> tuple[bool, str]:
    """A line from B must be side 'them', a line from A must not; a line from neither counts as wrong."""
    ok = n_a = n_b = 0
    for r in rows:
        o = origin(r.get("src", ""), ref_a, ref_b)
        n_a, n_b = n_a + (o == "a"), n_b + (o == "b")
        ok += (o == "b" and r.get("side") == "them") or (o == "a" and r.get("side") != "them")
    share = ok / len(rows) if rows else 0.0
    reasons = [x for x, bad in (("no line from clip A (mic)", not n_a), ("no line from clip B (system audio)", not n_b),
                                (f"labels right {share:.0%} < {MIN_LABEL_OK:.0%}", share < MIN_LABEL_OK)) if bad]
    msg = f"{ok}/{len(rows)} lines labelled right ({share:.0%}, limit {MIN_LABEL_OK:.0%}); from A {n_a}, from B {n_b}"
    return not reasons, msg + ("; " + "; ".join(reasons) if reasons else "")


def check_loopback(rows: list[dict], ref: str) -> tuple[bool, str]:
    good = [r for r in rows if _hit(r.get("src", ""), ref, "en") >= MIN_WORD_HIT]
    msg = f"{len(good)}/{len(rows)} loopback lines match the played speech"
    return bool(good), msg + ("" if good else "; nothing from the other process was transcribed")


def read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip() and '"src"' in x]


def read_latency(path) -> list[dict]:
    """#1247: the --latency-jsonl records (one per final line); [] when the file is missing or unreadable."""
    try:
        text = Path(path).read_text("utf-8", errors="replace")
    except OSError:  # missing or unreadable: the diagnostics never cost the check its verdict
        return []
    out = []
    for x in text.split("\n"):  # not splitlines(): a U+2028 inside a JSON string is not a record end
        try:
            r = json.loads(x)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


# ---------------------------------------------------------------- clips
def build_clips(data: Path, out: Path) -> dict:
    import numpy as np
    import soundfile as sf

    rows = [json.loads(x) for x in (data / "manifest.jsonl").read_text("utf-8").splitlines() if x.strip()]
    out.mkdir(parents=True, exist_ok=True)
    meta = {}

    def join(lang: str, target_s: float, name: str, start: int = 0) -> None:
        sel = [r for r in rows if r["lang"] == lang]
        audio = [sf.read(str(data / r["wav"]), dtype="float32", always_2d=True)[0].mean(axis=1) for r in sel]
        lay = layout([len(a) / SR for a in audio], target_s, start=start)
        x = np.zeros(int((lay[-1][2] + GAP_S) * SR), np.float32)
        for k, s, _e in lay:
            x[int(s * SR):int(s * SR) + len(audio[k])] = audio[k]
        sf.write(str(out / f"{name}.wav"), x, SR)
        meta[name] = {"wav": f"{name}.wav", "lang": lang, "seconds": round(len(x) / SR, 2),
                      "speech": [round(lay[0][1], 2), round(lay[-1][2], 2)],
                      "ref": " ".join(sel[k]["ref_text"] for k, _s, _e in lay), "clips": [sel[k]["id"] for k, _s, _e in lay],
                      # #1247: per-clip spans + references, so a FAIL can say which clips got no subtitle
                      "spans": [[round(s, 2), round(e, 2)] for _k, s, e in lay],
                      "refs": [sel[k]["ref_text"] for k, _s, _e in lay]}

    join("en", MIC_S, "mic_en")
    join("zh", MEET_S, "meet_a_zh")
    join("en", MEET_S, "meet_b_en", start=10)
    join("en", LOOP_S, "loop_en", start=5)
    (out / "clips.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    return meta


# ---------------------------------------------------------------- devices (owner PC only)
def real_speaker(sc) -> str:
    from tinglongwu.capture.wasapi import is_own_output

    d = sc.default_speaker()
    if d is not None and not is_own_output(d.name):
        return d.name
    names = [s.name for s in sc.all_speakers() if not is_own_output(s.name)]
    if not names:
        raise RuntimeError("no real speakers found")
    return names[0]


def play(wav: str, device: str | None) -> int:
    import numpy as np
    import soundcard as sc
    import soundfile as sf

    if sys.platform == "win32":
        from tinglongwu.capture._soundcard import patch_mixformat

        patch_mixformat()
    x, sr = sf.read(wav, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    y = np.interp(np.arange(int(len(x) * PLAY_SR / sr)) * sr / PLAY_SR, np.arange(len(x)), x).astype(np.float32)
    name = device or real_speaker(sc)
    spk = next(s for s in sc.all_speakers() if name.casefold() in s.name.casefold())
    print(f"PLAY {time.time():.3f} {spk.name}", flush=True)
    with spk.player(samplerate=PLAY_SR, channels=1) as p:
        p.play(y)
    return 0


class Child:
    """A child process whose stdout+stderr lines are kept; wait_for(marks) blocks until each mark was printed."""

    def __init__(self, cmd: list[str], log: Path):
        self.lines: list[str] = []
        self.log = open(log, "w", encoding="utf-8")
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace")
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        for ln in self.p.stdout:
            self.lines.append(ln)
            self.log.write(ln)
            self.log.flush()

    def finish(self, timeout: float = 60) -> None:
        """Wait for the exit (kill on timeout), then join the reader so every printed line is in self.lines (#1203)."""
        try:
            self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            kill_tree(self.p)
        self.reader.join(10)

    def close(self) -> None:
        """Always safe: terminate a still-running child, reap it, join the reader, close the log (#1203)."""
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                kill_tree(self.p)
        self.reader.join(10)
        self.log.close()

    def __enter__(self) -> Child:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def wait_for(self, *marks: str, timeout: float = READY_TIMEOUT_S) -> bool:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout and self.p.poll() is None:
            if all(any(m in ln for ln in self.lines) for m in marks):
                return True
            time.sleep(0.2)
        return False


def play_child(wav: Path, device: str | None) -> subprocess.Popen:
    """A separate player process (#348: the audio comes from another program, as from Discord)."""
    cmd = [sys.executable, str(Path(__file__).resolve()), "play", str(wav)] + (["--device", device] if device else [])
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)


def kill_tree(p: subprocess.Popen) -> None:
    """Kill p and everything it started (the verifier's tlwu --mic), then reap p (#1203 review)."""
    if p.poll() is None:
        try:
            import psutil

            for c in psutil.Process(p.pid).children(recursive=True):
                try:
                    c.kill()
                except psutil.Error:
                    pass
        except Exception:  # psutil missing or p already gone: fall through to the plain kill
            pass
        p.kill()
    p.wait()


def kill_proc(p: subprocess.Popen) -> None:
    kill_tree(p)
    if p.stdout:
        p.stdout.close()


def played_at(p: subprocess.Popen) -> float:
    try:
        out, _ = p.communicate(timeout=MIC_S * 3)
    except BaseException:
        kill_proc(p)
        raise
    ln = next((x for x in out.splitlines() if x.startswith("PLAY ")), None)
    if p.returncode != 0 or ln is None:
        raise RuntimeError(f"playback failed (exit {p.returncode}): {out.strip()[-300:]}")
    print(ln, flush=True)
    return float(ln.split()[1])


def _wer(rows: list[dict], ref: str) -> float:
    from tlwu_eval.scoring import error_rate

    return error_rate([(ref, " ".join(r["src"] for r in rows))], "en")


def run_mic(clips: Path, mdir: str, out: Path) -> tuple[bool, str]:
    meta = json.loads((clips / "clips.json").read_text("utf-8"))["mic_en"]
    wav, common = clips / meta["wav"], ["--lang", "en", "--transcript-only", "--models-dir", mdir]
    fj, mj, stop = out / "mic_file.jsonl", out / "mic_live.jsonl", out / "mic.stop"
    lj = out / "mic_latency.jsonl"  # #1247: per live line, speech end -> cut -> ASR -> shown
    for f in (fj, mj, stop, lj):
        f.unlink(missing_ok=True)
    subprocess.run([sys.executable, "-m", "tinglongwu", "--file", str(wav), "--save", str(fj), *common], check=True,
                   stdout=subprocess.DEVNULL)
    with Child([sys.executable, str(ROOT / "scripts" / "verify_offline.py"), "--mic", "--seconds", "3600",
                "--stop-file", str(stop), "--", "--capture-device", CABLE_OUT, "--save", str(mj),
                "--latency-jsonl", str(lj), *common],
               out / "mic_live.log") as ch:
        ready = False
        try:
            ready = ch.wait_for(READY_MARK)
            if not ready:
                return False, f"tlwu --mic did not get ready in {READY_TIMEOUT_S:.0f} s (see {out / 'mic_live.log'})"
            t0 = played_at(play_child(wav, CABLE_IN))
            time.sleep(TAIL_S)
        finally:
            # verify_offline has no SIGTERM handler: ask it to stop (it then ends the tlwu --mic it started) before
            # any terminate, so no orphan tlwu process is left on a ready timeout or exception (#1203 review).
            stop.write_text("stop")
            ch.finish(60)
            if not ready:  # #1247: what the live run said before it never got (wait_for: a late ready line still
                for ln in tlwu_notes(ch.lines):  # failed); after finish(), so lines read while it ended are in
                    print(f"diagnose: live {ln}", flush=True)
        offline_ok = any(ln.startswith("PASS") for ln in ch.lines)
    file_rows, mic_rows = read_jsonl(fj), read_jsonl(mj)
    offsets = wall_offsets(mic_rows, t0)
    gap = max_gap(offsets, *meta["speech"])
    ok, msg = check_mic(_wer(file_rows, meta["ref"]), _wer(mic_rows, meta["ref"]) if mic_rows else 1.0, gap,
                        len(mic_rows))
    diag = diagnose_mic(meta, file_rows, mic_rows, offsets)
    print(f"diagnose: {diag}", flush=True)  # #1247: kept out of the PASS/FAIL line (step summary stays one line)
    if mic_rows:
        print("diagnose: around the gap: " + gap_context(offsets, [r.get("src", "") for r in mic_rows],
                                                         *gap_span(offsets, *meta["speech"])), flush=True)
        lat_rows = read_latency(lj)
        srcs = [r.get("src", "") for r in mic_rows]
        print(f"diagnose: latency: {latency_summary(lat_rows)}", flush=True)
        for ln in latency_context(offsets, srcs, match_latency(srcs, lat_rows), *gap_span(offsets, *meta["speech"])):
            print(f"diagnose: gap line: {ln}", flush=True)
    for ln in tlwu_notes(ch.lines):
        print(f"diagnose: live {ln}", flush=True)
    (out / "mic_report.json").write_text(json.dumps(
        {"diagnose": diag, "mic_offsets_s": [round(x, 1) for x in offsets], "spans": meta.get("spans"),
         "file_src": [r.get("src", "") for r in file_rows], "mic_src": [r.get("src", "") for r in mic_rows]},
        ensure_ascii=False, indent=1), "utf-8")
    if not offline_ok:
        ok, msg = False, msg + "; verify_offline did not PASS (network connection or early exit)"
    return ok, msg


def run_file(clips: Path, mdir: str, out: Path, extra: list[str] | None = None) -> tuple[bool, str]:
    """#1247: only the --file half of the #894 check, with --record, on any machine (no audio devices): which clips of
    the joined recording get no subtitle even from the file, and what tlwu wrote over them. Diagnostic: PASS when
    tlwu ran and saved lines; the clip table goes to stdout and file_report.json."""
    import shutil

    meta = json.loads((clips / "clips.json").read_text("utf-8"))["mic_en"]
    fj, rdir = out / "file_only.jsonl", out / "file_rec"
    fj.unlink(missing_ok=True)
    (out / "file_report.json").unlink(missing_ok=True)  # a failed run must not leave an earlier run's table
    shutil.rmtree(rdir, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "tinglongwu", "--file", str(clips / meta["wav"]), "--save", str(fj),
                    "--record", str(rdir), "--lang", "en", "--transcript-only", "--models-dir", mdir, *(extra or [])],
                   check=True, stdout=subprocess.DEVNULL)
    rows = read_jsonl(fj)
    seg = rdir / "segments.jsonl"
    if not seg.is_file():  # no --record output: the clip table would call every clip unheard
        return False, f"{len(rows)} lines but no --record output ({seg}); clips not classified"
    rec = [json.loads(x) for x in seg.read_text("utf-8").splitlines() if x.strip()]
    per = clip_lines(meta.get("spans") or [], meta.get("refs") or [], rec)
    import soundfile as sf

    add_levels(per, sf.read(str(clips / meta["wav"]), dtype="float32", always_2d=True)[0].mean(axis=1))
    print(clip_table(per), flush=True)
    (out / "file_report.json").write_text(json.dumps({"clips": per, "rec": rec}, ensure_ascii=False, indent=1), "utf-8")
    wer = _wer(rows, meta["ref"]) if rows else 1.0
    missed = sum(r["hit"] < MIN_CLIP_HIT for r in per)
    return bool(rows), f"WER {wer:.3f}, {len(rows)} lines, clips missed {missed}/{len(per)}"


def meeting_child(proto_args: list[str]) -> int:
    """The meeting process with only the feedback guard off: its mic is CABLE Output on purpose (clip A)."""
    from tinglongwu.reverse import live, proto

    live.check_mic_feedback = lambda *x, **k: None
    return proto.main(proto_args)


def run_meeting(clips: Path, mdir: str, out: Path) -> tuple[bool, str]:
    import soundcard as sc

    meta = json.loads((clips / "clips.json").read_text("utf-8"))
    sj = out / "meeting.jsonl"
    sj.unlink(missing_ok=True)
    spk = real_speaker(sc)
    with Child([sys.executable, str(Path(__file__).resolve()), "_meeting-child", "--", "--mic", CABLE_OUT, "--to", "en",
                "--no-tts", "--no-confirm", "--models-dir", mdir, "--listen", "--capture-device", spk, "--lang", "auto",
                "--save", str(sj), "--models-dir", mdir], out / "meeting.log") as ch:
        if not ch.wait_for(READY_MARK, SPEAK_READY_MARK):
            return False, f"meeting mode did not get ready in {READY_TIMEOUT_S:.0f} s (see {out / 'meeting.log'})"
        players = [play_child(clips / meta["meet_a_zh"]["wav"], CABLE_IN)]
        try:
            players.append(play_child(clips / meta["meet_b_en"]["wav"], spk))
            for pl in players:
                played_at(pl)
        finally:
            for pl in players:
                kill_proc(pl)
        time.sleep(TAIL_S)
        ch.close()
    return check_meeting(read_jsonl(sj), meta["meet_a_zh"]["ref"], meta["meet_b_en"]["ref"])


def run_loopback(clips: Path, mdir: str, out: Path) -> tuple[bool, str]:
    meta = json.loads((clips / "clips.json").read_text("utf-8"))["loop_en"]
    sj = out / "loopback.jsonl"
    sj.unlink(missing_ok=True)
    with Child([sys.executable, "-m", "tinglongwu", "--lang", "en", "--transcript-only", "--save", str(sj),
                "--models-dir", mdir], out / "loopback.log") as ch:
        if not ch.wait_for(READY_MARK):
            return False, f"tlwu loopback did not get ready in {READY_TIMEOUT_S:.0f} s (see {out / 'loopback.log'})"
        played_at(play_child(clips / meta["wav"], None))
        time.sleep(TAIL_S)
        ch.close()
    return check_loopback(read_jsonl(sj), meta["ref"])


WHAT = {"file": "#1247 file baseline of the #894 clip", "mic": "#894 live mic (CABLE) vs file",
        "meeting": "#4 meeting mode ◀ Them labels",
        "loopback": "#348 other-process audio via WASAPI loopback"}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_meeting-child"]:
        return meeting_child(argv[2:] if argv[1:2] == ["--"] else argv[1:])
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clips")
    c.add_argument("--data", required=True, help="make_ci_smoke.py --out folder (manifest.jsonl + wavs)")
    c.add_argument("--out", required=True)
    pl = sub.add_parser("play")
    pl.add_argument("wav")
    pl.add_argument("--device", default=None)
    for name in WHAT:
        s = sub.add_parser(name)
        s.add_argument("--clips", required=True)
        s.add_argument("--models-dir", required=True)
        s.add_argument("--out", default=None, help="folder for logs and transcripts (default: --clips)")
        s.add_argument("--json", default=None)
        if name == "file":
            s.add_argument("extra", nargs="*", help="more tlwu arguments after `--` (e.g. --device cpu)")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "clips":
        meta = build_clips(Path(a.data), Path(a.out))
        print("clips: " + ", ".join(f"{k} {v['seconds']:.0f} s ({len(v['clips'])} FLEURS clips)" for k, v in meta.items()))
        return 0
    if a.cmd == "play":
        return play(a.wav, a.device)
    out = Path(a.out or a.clips)
    out.mkdir(parents=True, exist_ok=True)
    run = {"file": run_file, "mic": run_mic, "meeting": run_meeting, "loopback": run_loopback}[a.cmd]
    try:
        args = (a.extra,) if a.cmd == "file" else ()
        ok, msg = run(Path(a.clips), a.models_dir, out, *args)
    except Exception as e:  # noqa: BLE001 - one FAIL line in the step summary, not a traceback only
        ok, msg = False, f"could not run: {type(e).__name__}: {e}"
    if a.json:
        Path(a.json).write_text(json.dumps({"ok": ok, "result": msg}, ensure_ascii=False), "utf-8")
    print(f"{'PASS' if ok else 'FAIL'} {WHAT[a.cmd]}: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
