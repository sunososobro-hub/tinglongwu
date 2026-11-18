#!/usr/bin/env python3
"""#1342: CI check of the first-run hand-over - source subtitles while Hy-MT2 is still missing, translated lines after
it lands, in the SAME tlwu session (no restart).

Run by .github/workflows/install-clean.yml (job `mt-handover`, windows-latest) after
`tlwu-setup --default-listen --defer-mt` (what the setup wizard runs since v0.4.6: Whisper only):

  1. a long English clip (the bundled selftest clip repeated) is played to `tlwu --file ... --paced --mt-pending`, as
     the GUI starts listening while the model is still downloading (gui/core.py adds --mt-pending, #1375);
  2. the Hy-MT2 download is HELD until the first source-text line is written, then `--land-cmd` runs (the GUI's
     download child is `tlwu-setup --default-listen`, #1358). If Hy-MT2 is already complete (a cached models dir),
     it is held aside instead (GGUF renamed, manifest entry removed) and put back as the "landing";
  3. the session keeps running for --settle s after the first translated line (a fall-back to source fails), then
     the check ends; --timeout bounds the whole session.

PASS when: lines written before the landing started are source text only (no translation), at least one translated
(CJK, for --to zh) line comes after it, no line after the first translated one falls back to source text, and every
SAVED line has its source text. (Speech lost before tlwu writes a line leaves no row, so it is not detected here.) The verdict is printed and written to --out as JSON.

    python scripts/mt_handover_ci.py --models-dir M --land-cmd "tlwu-setup --default-listen" --out h.json

`--land-cmd` gets `--models-dir M` appended as a separate, properly quoted argument.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "tinglongwu" / "data" / "selftest_en.wav"
CJK = re.compile(r"[㐀-鿿豈-﫿]")


def is_translated(rec: dict, to: str = "zh") -> bool:
    """A saved line (tinglongwu.sink.JsonlSaveSink record) that carries a translation into `to`."""
    dst = (rec.get("dst") or "").strip()
    if not dst:
        return False
    return bool(CJK.search(dst)) if to == "zh" else dst != (rec.get("src") or "").strip()


def judge(records: list[dict], n_before_land: int | None, to: str = "zh") -> list[str]:
    """Problems with one hand-over run (empty = PASS).

    records: the --save JSONL lines in order. n_before_land: how many lines were written when the landing started
    (None = it never started, i.e. no source line ever came)."""
    problems: list[str] = []
    if n_before_land is None or n_before_land == 0:
        problems.append("no source-text line before the translation model landed (subtitles waited for Hy-MT2)")
    before, after = records[:n_before_land or 0], records[n_before_land or 0:]
    early = [r for r in before if is_translated(r, to)]
    if early:
        problems.append(f"{len(early)} translated line(s) before Hy-MT2 landed: the model was not really missing")
    first = next((i for i, r in enumerate(after) if is_translated(r, to)), None)
    if first is None:
        problems.append("no translated line after Hy-MT2 landed (no hand-over without a restart)")
    else:
        back = [r for r in after[first + 1:] if not is_translated(r, to)]
        if back:
            problems.append(f"{len(back)} source-only line(s) after the first translated line (hand-over not one-way)")
    lost = [r for r in records if not (r.get("src") or "").strip()]
    if lost:
        problems.append(f"{len(lost)} line(s) without source text")
    return problems


def long_clip(src: Path, dst: Path, seconds: float, gap: float = 1.0) -> float:
    """Write `src` repeated (with `gap` s of silence between) until it lasts >= `seconds`; returns its length."""
    with wave.open(str(src), "rb") as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    silence = b"\x00" * (int(params.framerate * gap) * params.sampwidth * params.nchannels)
    one = (len(frames) + len(silence)) / (params.framerate * params.sampwidth * params.nchannels)
    n = max(1, int(seconds / one + 0.999))
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        for _ in range(n):
            w.writeframes(frames)
            w.writeframes(silence)
    return n * one


def read_jsonl(path: Path) -> list[dict]:
    """The complete rows of a JSONL file another process is still appending to (a trailing partial row is left
    for the next read, so a half-written UTF-8 character never breaks the decode)."""
    out: list[dict] = []
    try:
        data = path.read_bytes()
    except OSError:
        return out
    for raw in data[: data.rfind(b"\n") + 1].split(b"\n"):  # LF only: U+2028 inside a subtitle is not a row end
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw.decode("utf-8", errors="replace")))
        except ValueError:
            pass
    return out


def with_args(command: str, args: list[str]):
    """A user-given command line plus extra args, for subprocess (a string on Windows, where shlex quoting differs)."""
    if os.name == "nt":
        return command + (" " + subprocess.list2cmdline(args) if args else "")
    return [*shlex.split(command), *args]


def _write_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1), "utf-8")
    os.replace(tmp, path)  # atomic: the manifest is never half-written


class Hold:
    """Hy-MT2 already on disk: move it aside now, put it back as the landing. `land()` is idempotent and resumes
    from whatever state an interrupted move left, so the cached models dir always ends up complete."""

    def __init__(self, mdir: Path):
        sys.path.insert(0, str(ROOT))
        from tinglongwu.hymt import HYMT_SIZES, hymt_path

        self.gguf, self.key = hymt_path(mdir, "1.8b"), HYMT_SIZES["1.8b"][0]
        self.held = self.gguf.with_name(self.gguf.name + ".held")
        self.mf = mdir / "manifest.json"
        self.man = json.loads(self.mf.read_text("utf-8"))
        os.replace(self.gguf, self.held)
        try:
            _write_json(self.mf, {k: v for k, v in self.man.items() if k != self.key})
        except BaseException:
            self.land()
            raise

    def land(self) -> int:
        if self.held.exists():
            os.replace(self.held, self.gguf)
        _write_json(self.mf, self.man)
        return 0


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def run(a) -> dict:
    sys.path.insert(0, str(ROOT))
    from tinglongwu.mt_pending import hymt_complete

    mdir = Path(a.models_dir)
    cached = hymt_complete(mdir)
    if not cached and not a.land_cmd:
        raise SystemExit("Hy-MT2 is not installed and no --land-cmd downloads it")
    with tempfile.TemporaryDirectory(prefix="tlwu-handover-") as tmp:
        work = Path(tmp)
        clip = work / "clip.wav"
        clip_s = long_clip(Path(a.clip), clip, a.timeout + 60)  # before the hold: a bad --clip leaves the cache alone
        save, log = work / "lines.jsonl", Path(a.log)
        rest = ["--file", str(clip), "--paced", "--mt-pending", "--lang", "en", "--to", a.to, "--no-splash",
                "--no-adapt", "--save", str(save), "--models-dir", str(mdir)]
        cmd = with_args(a.tlwu, rest) if a.tlwu else [sys.executable, "-m", "tinglongwu", *rest]
        print("run:", cmd, flush=True)
        t0 = time.monotonic()
        deadline = t0 + a.timeout
        landing: dict = {"mode": "hold" if cached else "download"}
        hold = None
        proc = lander = None
        n_before = None
        stop_asked = False
        try:
            hold = Hold(mdir) if cached else None
            with open(log, "w", encoding="utf-8", errors="replace") as lf:
                proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
                settle_until = None
                settled = False
                n_first_tr = 0
                while time.monotonic() < deadline and proc.poll() is None:
                    recs = read_jsonl(save)
                    if lander is None and n_before is None and any((r.get("src") or "").strip() for r in recs):
                        n_before = len(recs)
                        before_rows = [dict(r) for r in recs]  # #1395 may rewrite the last row in place later
                        landing["started_at"] = round(time.monotonic() - t0, 1)
                        print(f"first source line after {landing['started_at']} s: landing Hy-MT2 "
                              f"({landing['mode']})", flush=True)
                        if hold is not None:
                            landing.update(rc=hold.land(), seconds=0.0, done_at=landing["started_at"])
                        else:
                            land_t = time.monotonic()
                            try:
                                lander = subprocess.Popen(with_args(a.land_cmd, ["--models-dir", str(mdir)]))
                            except OSError as e:
                                landing.update(rc=None, error=f"landing command did not start: {e}")
                                lander = False  # never retried
                    if lander is not None and "rc" not in landing and lander.poll() is not None:
                        landing.update(rc=lander.returncode, seconds=round(time.monotonic() - land_t, 1),
                                       done_at=round(time.monotonic() - t0, 1))
                    if settle_until is None and n_before is not None \
                            and any(is_translated(r, a.to) for r in recs[n_before:]):
                        settle_until = time.monotonic() + a.settle  # keep listening: no fall-back to source
                        n_first_tr = sum(is_translated(r, a.to) for r in recs[n_before:])
                    if "rc" in landing and landing["rc"] != 0:
                        break  # landing failed: this run cannot pass, report it now
                    if settle_until is not None and time.monotonic() >= settle_until and "rc" in landing:
                        # tlwu must still be running at the boundary and have translated more lines in the window
                        recs = read_jsonl(save)  # fresh snapshot at the boundary
                        more = sum(is_translated(r, a.to) for r in recs[n_before:]) > n_first_tr
                        settled = proc.poll() is None and more
                        break
                    time.sleep(1.0)
                stop_asked = proc.poll() is None  # still running: the checker ends it; else tlwu exited on its own
                if lander and "rc" not in landing and lander.poll() is None:  # observed before anything is stopped
                    landing["pending_at_end"] = True
        finally:
            try:
                try:
                    if proc is not None:
                        exited_early = proc.poll() is not None and not stop_asked
                        _stop(proc)
                finally:
                    if lander:  # stop the download even if stopping tlwu failed
                        if "rc" not in landing and lander.poll() is None:
                            if time.monotonic() >= deadline:
                                landing["timed_out"] = True
                            else:
                                landing["cancelled"] = "check ended before the download finished"
                        _stop(lander)
                        landing.setdefault("rc", lander.returncode)
            finally:
                if hold is not None:
                    hold.land()  # never leave the cached models dir without its model, even if a stop failed
        recs = read_jsonl(save)
    if n_before:
        # a pre-landing source row that a late fragment rewrote in place (#1395) after the landing counts as
        # post-landing output, not as a translation before the model landed
        changed = [i for i, old in enumerate(before_rows)
                   if i < len(recs) and not is_translated(old, a.to) and is_translated(recs[i], a.to)]
        if changed:
            n_before = min(changed)
    problems = judge(recs, n_before, a.to)
    if n_before is not None and (landing.get("rc") != 0 or landing.get("pending_at_end")):
        problems.append(f"landing did not finish cleanly: {landing}")
    saw_translation = n_before is not None and any(is_translated(r, a.to) for r in recs[n_before:])
    if saw_translation and not settled:  # also when tlwu exited before a poll even saw the translated line
        problems.append(f"did not watch a full {a.settle:g} s of further translated lines after the first one "
                        "(deadline, tlwu exit, or no new translated line): a fall-back to source text could be missed")
    if proc is not None and exited_early and proc.returncode != 0:
        problems.append(f"tlwu exited with code {proc.returncode} before the check ended")
    first_tr = next((r for r in recs[n_before or 0:] if is_translated(r, a.to)), None)
    return {"verdict": "FAIL" if problems else "PASS", "problems": problems, "lines": len(recs),
            "source_lines_before_landing": n_before, "landing": landing, "clip_seconds": round(clip_s, 1),
            "first_source": recs[0] if recs else None, "first_translated": first_tr,
            "elapsed_seconds": round(time.monotonic() - t0, 1), "exit": proc.returncode if proc else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models-dir", required=True)
    ap.add_argument("--land-cmd", default="", help="downloads Hy-MT2 (when it is not on disk yet)")
    ap.add_argument("--tlwu", default="", help="the tlwu command (default: this python -m tinglongwu)")
    ap.add_argument("--clip", default=str(CLIP))
    ap.add_argument("--to", default="zh")
    ap.add_argument("--timeout", type=float, default=900.0, help="seconds for the whole session (default 900)")
    ap.add_argument("--settle", type=float, default=30.0,
                    help="seconds to keep listening after the first translated line (a fall-back to source fails)")
    ap.add_argument("--log", default="mt_handover.log")
    ap.add_argument("--out", default="mt_handover.json")
    ap.add_argument("--summary", default=None, help="append a markdown block here (GITHUB_STEP_SUMMARY)")
    a = ap.parse_args(argv)
    r = run(a)
    text = json.dumps(r, ensure_ascii=False, indent=1)
    Path(a.out).write_text(text, "utf-8")
    print(text)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            f.write(f"### #1342 first-run hand-over (source subtitles -> translation without restart): {r['verdict']}\n"
                    f"```\n{text}\n```\n")
    print(f"mt-handover {r['verdict']}" + (": " + "; ".join(r["problems"]) if r["problems"] else ""), flush=True)
    return 1 if r["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
