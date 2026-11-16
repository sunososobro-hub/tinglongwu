#!/usr/bin/env python
"""#1194: three owner hand-tests on the owner-PC hw-tests runner, with recorded speech instead of a person.

    python scripts/bench/make_ci_smoke.py --pins eval/matrix_pins.json --out DATA     # pinned FLEURS clips (sha256)
    python scripts/hw_live_checks.py clips --data DATA --out CLIPS
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


def wall_offsets(rows: list[dict], t0: float) -> list[float]:
    """Seconds after epoch t0 at which each saved line (--save "wall", ISO 8601) was written."""
    return [datetime.datetime.fromisoformat(r["wall"]).timestamp() - t0 for r in rows if r.get("wall")]


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
                      "ref": " ".join(sel[k]["ref_text"] for k, _s, _e in lay), "clips": [sel[k]["id"] for k, _s, _e in lay]}

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
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for ln in self.p.stdout:
            self.lines.append(ln)
            self.log.write(ln)
            self.log.flush()

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


def played_at(p: subprocess.Popen) -> float:
    out, _ = p.communicate(timeout=MIC_S * 3)
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
    for f in (fj, mj, stop):
        f.unlink(missing_ok=True)
    subprocess.run([sys.executable, "-m", "tinglongwu", "--file", str(wav), "--save", str(fj), *common], check=True,
                   stdout=subprocess.DEVNULL)
    ch = Child([sys.executable, str(ROOT / "scripts" / "verify_offline.py"), "--mic", "--seconds", "3600",
                "--stop-file", str(stop), "--", "--capture-device", CABLE_OUT, "--save", str(mj), *common],
               out / "mic_live.log")
    if not ch.wait_for(READY_MARK):
        return False, f"tlwu --mic did not get ready in {READY_TIMEOUT_S:.0f} s (see {out / 'mic_live.log'})"
    t0 = played_at(play_child(wav, CABLE_IN))
    time.sleep(TAIL_S)
    stop.write_text("stop")
    ch.p.wait(60)
    offline_ok = any(ln.startswith("PASS") for ln in ch.lines)
    file_rows, mic_rows = read_jsonl(fj), read_jsonl(mj)
    gap = max_gap(wall_offsets(mic_rows, t0), *meta["speech"])
    ok, msg = check_mic(_wer(file_rows, meta["ref"]), _wer(mic_rows, meta["ref"]) if mic_rows else 1.0, gap,
                        len(mic_rows))
    if not offline_ok:
        ok, msg = False, msg + "; verify_offline did not PASS (network connection or early exit)"
    return ok, msg


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
    ch = Child([sys.executable, str(Path(__file__).resolve()), "_meeting-child", "--", "--mic", CABLE_OUT, "--to", "en",
                "--no-tts", "--no-confirm", "--models-dir", mdir, "--listen", "--capture-device", spk, "--lang", "auto",
                "--save", str(sj), "--models-dir", mdir], out / "meeting.log")
    if not ch.wait_for(READY_MARK, SPEAK_READY_MARK):
        return False, f"meeting mode did not get ready in {READY_TIMEOUT_S:.0f} s (see {out / 'meeting.log'})"
    pa, pb = play_child(clips / meta["meet_a_zh"]["wav"], CABLE_IN), play_child(clips / meta["meet_b_en"]["wav"], spk)
    played_at(pa), played_at(pb)
    time.sleep(TAIL_S)
    ch.p.terminate()
    ch.p.wait(60)
    return check_meeting(read_jsonl(sj), meta["meet_a_zh"]["ref"], meta["meet_b_en"]["ref"])


def run_loopback(clips: Path, mdir: str, out: Path) -> tuple[bool, str]:
    meta = json.loads((clips / "clips.json").read_text("utf-8"))["loop_en"]
    sj = out / "loopback.jsonl"
    sj.unlink(missing_ok=True)
    ch = Child([sys.executable, "-m", "tinglongwu", "--lang", "en", "--transcript-only", "--save", str(sj),
                "--models-dir", mdir], out / "loopback.log")
    if not ch.wait_for(READY_MARK):
        return False, f"tlwu loopback did not get ready in {READY_TIMEOUT_S:.0f} s (see {out / 'loopback.log'})"
    played_at(play_child(clips / meta["wav"], None))
    time.sleep(TAIL_S)
    ch.p.terminate()
    ch.p.wait(60)
    return check_loopback(read_jsonl(sj), meta["ref"])


WHAT = {"mic": "#894 live mic (CABLE) vs file", "meeting": "#4 meeting mode ◀ Them labels",
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
    run = {"mic": run_mic, "meeting": run_meeting, "loopback": run_loopback}[a.cmd]
    try:
        ok, msg = run(Path(a.clips), a.models_dir, out)
    except Exception as e:  # noqa: BLE001 - one FAIL line in the step summary, not a traceback only
        ok, msg = False, f"could not run: {type(e).__name__}: {e}"
    if a.json:
        Path(a.json).write_text(json.dumps({"ok": ok, "result": msg}, ensure_ascii=False), "utf-8")
    print(f"{'PASS' if ok else 'FAIL'} {WHAT[a.cmd]}: {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
