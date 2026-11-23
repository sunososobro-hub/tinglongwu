"""Speak-mode CPU readiness report (#1741): latency and phantom lines on a CPU-only machine, numbers only.

    python -m tlwu_eval.speak_ready --models-dir ~/tlwu-models --kokoro-python ~/tts-kokoro/bin/python \
        --kokoro-model ~/tlwu-models/kokoro --runs 10 --json ready.json --md ready.md

What runs: the speak pipeline (`python -m tinglongwu.reverse.proto --input X --paced --no-confirm --to en`, the
same VAD -> Whisper -> Hy-MT2 -> Kokoro path as `--mic`, the file released in real time) with `--no-play`, so no
audio device is needed. Per spoken line the #434 `--latency-jsonl` record gives `total_first_audio` = end of
speech -> first TTS audio handed to the output.
- script: SCRIPT (20 zh sentences, en references), synthesized with Kokoro zf_xiaobei, seeded 1.5-2.0 s gaps;
  `runs` paced runs of the same clip -> p50 / p95 of total_first_audio over all lines of all runs, chrF of the sent
  en lines vs the references (per run, whole text: line splits differ from run to run).
- noise: 60 s of digital silence + 60 s of synthetic keyboard noise (tlwu_eval.speak_noise, seed 968) -> lines
  sent (target 0) and how many of those a countdown / auto send mode would hold (greyed) for an explicit confirm
  (reverse.sendmode.low_confidence with the #759 hallucination flag; the CLI path itself sends every line).
No thresholds: the report never fails on a number (rules section 20).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .scoring import chrf, percentile

SR = 16000
SEED = 1741
GAP_S = (1.5, 2.0)
SILENCE_S = NOISE_S = 60.0
SCRIPT = (
    ("大家早安，我們現在開始今天的會議。", "Good morning everyone, let's start today's meeting."),
    ("首先請大家看一下上週的進度報告。", "First, please take a look at last week's progress report."),
    ("這個專案目前比預定的時間晚了兩天。", "This project is currently two days behind schedule."),
    ("主要的原因是測試環境出了一些問題。", "The main reason is that there were some problems with the test environment."),
    ("我們已經在星期三把問題修好了。", "We fixed the problem on Wednesday."),
    ("接下來我想討論一下新的功能需求。", "Next, I would like to discuss the new feature requirements."),
    ("客戶希望在下個月底之前看到第一個版本。", "The customer wants to see the first version before the end of next month."),
    ("我覺得這個時間有點緊，但是可以試試看。", "I think the timeline is a bit tight, but we can give it a try."),
    ("請問設計團隊那邊有什麼想法嗎？", "Does the design team have any thoughts?"),
    ("如果需要更多人手，請提早告訴我。", "If you need more people, please tell me early."),
    ("我們的預算還剩下大約三成。", "We have about thirty percent of the budget left."),
    ("下週二我會跟客戶再開一次會。", "I will have another meeting with the customer next Tuesday."),
    ("到時候我會把大家的意見整理好帶過去。", "I will organize everyone's feedback and bring it with me."),
    ("另外，伺服器的費用這個月又增加了。", "Also, the server costs went up again this month."),
    ("我們可能要檢查一下有沒有浪費的地方。", "We may need to check whether there is any waste."),
    ("有沒有人可以負責這件事情？", "Can someone take charge of this?"),
    ("好的，那就麻煩你了，謝謝。", "Okay, then I'll leave it to you, thank you."),
    ("最後提醒大家，週五下午公司會停電。", "Finally, a reminder that the office will have a power outage on Friday afternoon."),
    ("請記得在那之前把資料存好。", "Please remember to save your data before then."),
    ("今天的會議就到這裡，謝謝大家。", "That's all for today's meeting, thank you all."),
)
KOKORO_CODE = (  # runs with the Kokoro venv's python: argv = model dir, out dir, sentences...
    "import sys, soundfile as sf\n"
    "from kokoro_onnx import Kokoro\n"
    "d = sys.argv[1]\n"
    "k = Kokoro(d + '/kokoro-v1.0.int8.onnx', d + '/voices-v1.0.bin')\n"
    "for i, t in enumerate(sys.argv[3:]):\n"
    "    a, sr = k.create(t, voice='zf_xiaobei', speed=1.0, lang='cmn')\n"
    "    sf.write(f'{sys.argv[2]}/{i:02d}.wav', a, sr)\n")


def gaps(n: int, seed: int = SEED) -> list[float]:
    """n seeded pauses in GAP_S (seconds, 2 decimals): the same clip every run and every job."""
    rng = np.random.default_rng(seed)
    return [round(float(g), 2) for g in rng.uniform(GAP_S[0], GAP_S[1], n)]


def to16k(x: np.ndarray, sr: int) -> np.ndarray:
    from math import gcd

    from scipy.signal import resample_poly

    x = np.asarray(x, np.float32).reshape(-1)
    if sr == SR:
        return x
    g = gcd(sr, SR)
    return resample_poly(x, SR // g, sr // g).astype(np.float32)


def join(parts: list[np.ndarray], pauses: list[float], lead: float = 1.0, tail: float = 3.0) -> np.ndarray:
    """lead silence, part0, pause0, part1, pause1, ..., tail silence (16 kHz float32)."""
    z = lambda s: np.zeros(int(s * SR), np.float32)  # noqa: E731
    out = [z(lead)]
    for p, g in zip(parts, pauses):
        out += [np.asarray(p, np.float32), z(g)]
    return np.concatenate(out + [z(tail)])


def script_clip(out: Path, kokoro_python: str, kokoro_model: str) -> Path:
    import soundfile as sf

    with tempfile.TemporaryDirectory(prefix="tlwu-ready-") as td:
        subprocess.run([kokoro_python, "-c", KOKORO_CODE, kokoro_model, td, *(zh for zh, _ in SCRIPT)], check=True)
        parts = [to16k(*sf.read(str(p), dtype="float32")) for p in sorted(Path(td).glob("*.wav"))]
    sf.write(str(out), join(parts, gaps(len(parts))), SR)
    return out


def noise_clip(out: Path) -> Path:
    import soundfile as sf

    from .speak_noise import synth

    sf.write(str(out), np.concatenate([np.zeros(int(SILENCE_S * SR), np.float32), synth("keyboard", NOISE_S)]), SR)
    return out


def speak_cmd(wav: Path, jsonl: Path, report: Path, a) -> list[str]:
    cmd = [sys.executable, "-m", "tinglongwu.reverse.proto", "--input", str(wav), "--paced", "--no-confirm",
           "--no-play", "--no-vmic", "--compute", "cpu", "--to", "en", "--tts-engine", "kokoro",
           "--tts-python", a.kokoro_python, "--tts-model", a.kokoro_model, "--speaker", "0",
           "--latency-jsonl", str(jsonl), "--report", str(report)]
    if a.models_dir:
        cmd += ["--models-dir", a.models_dir]
    return cmd + (["--whisper", a.whisper] if a.whisper else [])


def run_one(wav: Path, work: Path, tag: str, a) -> dict:
    jsonl, report = work / f"{tag}.jsonl", work / f"{tag}.json"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "TLWU_NO_UPDATE": "1"}
    try:
        p = subprocess.run(speak_cmd(wav, jsonl, report, a), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=a.timeout)
        rc, err = p.returncode, (p.stderr or p.stdout or "")[-400:]
    except subprocess.TimeoutExpired:  # keep the runs already done: this one counts as an error, the report goes on
        rc, err = -1, f"timeout after {a.timeout}s"
    recs = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if jsonl.is_file() else []
    items = json.loads(report.read_text(encoding="utf-8")).get("items", []) if report.is_file() else []
    out = {"rc": rc, "records": recs, "items": items}
    if rc:
        out["error"] = err or f"exit code {rc}"
    return out


def held(zh: str) -> bool:
    """True when a countdown / auto send mode would hold this line for an explicit confirm (greyed)."""
    from tinglongwu.hallucination import is_hallucination
    from tinglongwu.reverse.sendmode import low_confidence

    u = SimpleNamespace(zh=zh, back="", extra={"halluc": is_hallucination(zh, strict=True)})
    return bool(low_confidence(u, require_back=False))


def summarize(runs: list[dict], noise: dict, script=SCRIPT) -> dict:
    fa = [r["total_first_audio"] for run in runs for r in run["records"] if r.get("total_first_audio") is not None]
    ref = " ".join(en for _, en in script)
    per_run = []
    for run in runs:
        sent = [it for it in run["items"] if it.get("decision") == "sent"]
        hyp = " ".join(str(it.get("en", "")) for it in sent)
        per_run.append({"lines": len(run["items"]), "sent": len(sent), "rc": run["rc"],
                        "chrf": round(chrf([(ref, hyp)]), 1)})
    nsent = [it for it in noise["items"] if it.get("decision") == "sent"]
    cf = [r["chrf"] for r in per_run]
    return {
        "runs": len(runs), "sentences": len(script),
        "first_audio_s": {"n": len(fa), "p50": percentile(fa, 50) if fa else None,
                          "p95": percentile(fa, 95) if fa else None, "max": max(fa) if fa else None},
        "chrf": {"median": float(np.median(cf)) if cf else None, "min": min(cf) if cf else None},
        "per_run": per_run,
        "noise": {"seconds": SILENCE_S + NOISE_S, "lines": len(noise["items"]), "sent": len(nsent),
                  "held": sum(held(str(it.get("zh", ""))) for it in nsent), "rc": noise["rc"]},
        "errors": [r["error"] for r in runs + [noise] if r.get("error")],
    }


def markdown(s: dict, where: str = "") -> str:
    fa, n, c = s["first_audio_s"], s["noise"], s["chrf"]
    rows = "\n".join(f"| {i} | {r['sent']}/{r['lines']} | {r['chrf']} | {r['rc']} |"
                     for i, r in enumerate(s["per_run"], 1))
    return "\n".join([
        f"### speak readiness (CPU){' - ' + where if where else ''}",
        f"- (1) speech end -> TTS first audio over {fa['n']} lines of {s['runs']} paced runs "
        f"({s['sentences']} zh sentences, {GAP_S[0]}-{GAP_S[1]} s gaps): p50 {fa['p50']} s, p95 {fa['p95']} s, "
        f"max {fa['max']} s",
        f"- (2) {n['seconds']:.0f} s silence + keyboard noise: {n['sent']} lines sent (target 0), "
        f"{n['held']} of them held/greyed by countdown/auto mode",
        f"- (3) zh -> en chrF of sent lines vs script: median {c['median']}, min {c['min']}",
        "", "| run | sent/lines | chrF | rc |", "|---|---|---|---|", rows,
    ] + ([f"\nerrors: {len(s['errors'])} (first: `{s['errors'][0][-200:]}`)"] if s["errors"] else []))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="speak-mode CPU readiness report (#1741), numbers only")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--kokoro-python", required=True)
    ap.add_argument("--kokoro-model", required=True)
    ap.add_argument("--whisper", default=None, help="default: what speak mode picks on the CPU")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per paced run")
    ap.add_argument("--where", default="", help="label for the report heading (runner, run link)")
    ap.add_argument("--json", default=None)
    ap.add_argument("--md", default=None)
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="tlwu-ready-") as td:
        work = Path(td)
        clip = script_clip(work / "script.wav", a.kokoro_python, a.kokoro_model)
        runs = []
        for i in range(a.runs):
            runs.append(run_one(clip, work, f"run{i}", a))
            print(f"run {i + 1}/{a.runs}: rc={runs[-1]['rc']} lines={len(runs[-1]['records'])}", flush=True)
        noise = run_one(noise_clip(work / "noise.wav"), work, "noise", a)
    s = summarize(runs, noise)
    md = markdown(s, a.where)
    if a.json:
        Path(a.json).write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.md:
        Path(a.md).write_text(md + "\n", encoding="utf-8")
    print(md)
    return 0 if not s["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
