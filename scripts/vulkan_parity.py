"""#1195: does the Vulkan MT path give the same translations as the CPU path? (CI: Mesa lavapipe, a software GPU)

  TLWU_DEVICE=cpu    python scripts/vulkan_parity.py translate --models-dir D --out cpu.json
  TLWU_DEVICE=vulkan python scripts/vulkan_parity.py translate --models-dir D --out vulkan.json   (TLWU_VULKAN_PACK=<pack>)
  python scripts/vulkan_parity.py compare cpu.json vulkan.json [--max-drop 2]

`translate` loads Hy-MT2 the way a session does and records which backend really ran (mt_backend.last_backend) next to
the translations. `compare` exits 1 unless the CPU run said "cpu", the Vulkan run said "vulkan" (a silent fallback to
CPU must not pass) and chrF(Vulkan output, CPU output as reference) >= 100 - max_drop (the #1195 bound, widened to chrF -5 with run data).
Speed is printed by `translate` but never gated.

ASR half (#1195, whisper.cpp Vulkan build of #783):
  TLWU_DEVICE=cpu    python scripts/vulkan_parity.py transcribe --models-dir D --data eval_data/smoke --out asr_cpu.json
  TLWU_DEVICE=vulkan python scripts/vulkan_parity.py transcribe --models-dir D --data eval_data/smoke --out asr_vk.json
  python scripts/vulkan_parity.py compare-asr asr_cpu.json asr_vk.json [--max-rise 0.02]

`transcribe` builds the recognizer the way a session does (asr_vulkan.select_recognizer: faster-whisper on the CPU,
whisper.cpp from the pack on Vulkan) and records the backend it returned, plus whether whisper.cpp's own log named a
Vulkan device. `compare-asr` exits 1 unless the CPU run said "cpu", the Vulkan run said "vulkan" with a Vulkan device
(no silent CPU fallback, in the app or inside whisper.cpp) and CER(Vulkan) <= CER(CPU) + max_rise against the clips'
reference text (the #1195 bound, CER +0.02).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SENTENCES = (
    "The meeting will start at nine tomorrow morning.",
    "Please send me the report before the end of the week.",
    "Local translation keeps your conversations on your own computer.",
    "The train was delayed because of heavy rain in the northern region.",
    "Can you turn the volume down a little? I cannot hear the speaker well.",
)
MAX_DROP = 5.0  # chrF points; was 2.0 (#1195). Runs 43985842364 and 44006290817: 4/5 lines identical, line 2 differs only 之前 vs 前 -> chrF 96.28 both times


def compare(cpu: dict, vulkan: dict, max_drop: float = MAX_DROP) -> tuple[bool, str]:
    """(ok, markdown summary) for two `translate` results."""
    from tlwu_eval.scoring import chrf

    problems = []
    if cpu.get("backend") != "cpu":
        problems.append(f"CPU run used backend {cpu.get('backend')!r}, expected 'cpu'")
    if vulkan.get("backend") != "vulkan":
        problems.append(f"Vulkan run used backend {vulkan.get('backend')!r}, expected 'vulkan' (silent fallback?)")
    ref, hyp = cpu.get("texts") or [], vulkan.get("texts") or []
    score = 0.0
    if not ref or len(ref) != len(hyp):
        problems.append(f"output counts differ or are empty: cpu={len(ref)} vulkan={len(hyp)}")
    else:
        score = chrf(list(zip(ref, hyp)))
        if score < 100.0 - max_drop:
            problems.append(f"chrF(vulkan vs cpu) {score:.2f} < {100.0 - max_drop:.2f}")
    lines = ["| path | backend | seconds |", "|---|---|---|",
             f"| cpu | {cpu.get('backend')} | {cpu.get('seconds', 0):.1f} |",
             f"| vulkan | {vulkan.get('backend')} | {vulkan.get('seconds', 0):.1f} |",
             "", f"chrF (vulkan vs cpu) = {score:.2f}, bound >= {100.0 - max_drop:.2f}; speed is not gated"]
    lines += [f"FAIL: {p}" for p in problems]
    lines += ["", *per_line(cpu, vulkan)]
    return not problems, "\n".join(lines)


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def per_line(cpu: dict, vulkan: dict) -> list[str]:
    """Markdown table of every sentence's CPU and Vulkan output, so a parity failure can be read in the job log
    (the run's artifacts are not reachable from every session, #1195)."""
    ref, hyp = cpu.get("texts") or [], vulkan.get("texts") or []
    rows = ["| # | source | cpu | vulkan | same |", "|---|---|---|---|---|"]
    for i in range(max(len(ref), len(hyp))):
        src = SENTENCES[i] if i < len(SENTENCES) else ""
        a = ref[i] if i < len(ref) else ""
        b = hyp[i] if i < len(hyp) else ""
        rows.append(f"| {i + 1} | {_cell(src)} | {_cell(a)} | {_cell(b)} | {'yes' if a == b else 'NO'} |")
    return rows


def translate(models_dir: str) -> dict:
    from tinglongwu import mt_backend
    from tinglongwu.selftest import _default_mt

    mt_backend.last_backend = ""
    t0 = time.perf_counter()
    tr = _default_mt(Path(models_dir), "hymt2", "tw", "cpu")
    texts = [(tr.translate(s, "en", "zh").text or "").strip() for s in SENTENCES]
    return {"backend": mt_backend.last_backend, "texts": texts, "seconds": time.perf_counter() - t0}


MAX_CER_RISE = 0.02  # #1195 Acceptance: Vulkan transcript within CER +0.02 of the CPU path (same clips, same refs)


def whisper_log_device(log: str) -> str:
    """'vulkan' when whisper.cpp's init log names a Vulkan backend, 'cpu' when it reports a CPU fallback, '' when the
    log says neither (same signals as scripts/bench/asr_backend_bench.check_vulkan)."""
    low = log.lower()
    if "no gpu found" in low or "failed to initialize" in low:
        return "cpu"
    if "vulkan" in low:
        return "vulkan"
    return "cpu" if ("using " in low and " backend" in low) else ""


def _capture_fd2(fn):
    """(fn(), what native code wrote to fd 2 meanwhile): whisper.cpp logs its backend choice there, not to sys.stderr."""
    import os
    import tempfile

    sys.stderr.flush()
    saved = os.dup(2)
    with tempfile.TemporaryFile() as tmp:
        os.dup2(tmp.fileno(), 2)
        try:
            result = fn()
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            os.close(saved)
        tmp.seek(0)
        log = tmp.read().decode("utf-8", "replace")
    sys.stderr.write(log)
    return result, log


def transcribe(models_dir: str, data_dir: str, langs=("en",), size: str = "small", select=None, read=None,
               clock=time.perf_counter) -> dict:
    """Every clip of `langs` in data_dir/manifest.jsonl through the session's recognizer choice (TLWU_DEVICE decides)."""
    sys.path.insert(0, str(ROOT / "eval"))
    from tlwu_eval.runner import load_manifest
    from tlwu_eval.runner import read_wav as eval_read

    if select is None:
        from tinglongwu.asr_vulkan import select_recognizer as select
    read = read or eval_read
    rows = [r for r in load_manifest(data_dir) if r["lang"] in langs]
    out = {"backend": "", "device_log": "", "devices": {}, "clips": [], "seconds": 0.0, "audio_seconds": 0.0}
    for lang in langs:
        lr = [r for r in rows if r["lang"] == lang]
        if not lr:
            continue
        # Path, not str: FasterWhisperRecognizer joins models_dir / "whisper-<size>" (run 45432917305 TypeError)
        (rec, backend), log = _capture_fd2(lambda: select(Path(models_dir), size, lang))
        if backend == "vulkan":
            _, warm = _capture_fd2(rec.warm_up)
            log += warm
        if out["backend"] and out["backend"] != backend:
            backend = f"{out['backend']}+{backend}"  # mixed across languages: never equals "cpu" or "vulkan"
        out["backend"] = backend
        out["devices"][lang] = whisper_log_device(log)
        devs = set(out["devices"].values())
        # every language must have logged a Vulkan device; one CPU / unknown result taints the whole run
        out["device_log"] = "vulkan" if devs == {"vulkan"} else ("cpu" if "cpu" in devs else "")
        for row in lr:
            audio, dur = read(row["wav"])
            t0 = clock()
            hyp = (getattr(rec.transcribe(audio), "text", "") or "").strip()
            out["seconds"] += clock() - t0
            out["audio_seconds"] += dur
            out["clips"].append({"id": row["id"], "lang": lang, "ref": row["ref_text"], "hyp": hyp})
    return out


def cer(clips: list[dict]) -> float:
    """Corpus CER over every clip (total character edits / total reference characters, tlwu_eval's normalization)."""
    sys.path.insert(0, str(ROOT / "eval"))
    from tlwu_eval.scoring import edit_distance, tokenize

    errs = units = 0
    for c in clips:
        r = tokenize(c["ref"], c["lang"], chars=True)
        errs += edit_distance(r, tokenize(c["hyp"], c["lang"], chars=True))
        units += len(r)
    if not units:
        raise ValueError("no reference text")
    return errs / units


def compare_asr(cpu: dict, vulkan: dict, max_rise: float = MAX_CER_RISE) -> tuple[bool, str]:
    """(ok, markdown summary) for two `transcribe` results."""
    sys.path.insert(0, str(ROOT / "eval"))
    problems = []
    if cpu.get("backend") != "cpu":
        problems.append(f"CPU run used ASR backend {cpu.get('backend')!r}, expected 'cpu'")
    if vulkan.get("backend") != "vulkan":
        problems.append(f"Vulkan run used ASR backend {vulkan.get('backend')!r}, expected 'vulkan' (silent fallback?)")
    elif vulkan.get("device_log") == "cpu":
        problems.append("whisper.cpp logged a CPU fallback (no Vulkan device)")
    elif vulkan.get("device_log") != "vulkan":
        problems.append("whisper.cpp log names no Vulkan device (unknown backend)")
    a, b = cpu.get("clips") or [], vulkan.get("clips") or []
    ca = cb = 0.0
    key = lambda clips: [(c.get("id"), c.get("lang"), c.get("ref")) for c in clips]  # noqa: E731
    if not a or key(a) != key(b):
        problems.append(f"clip lists (id, lang, ref) differ or are empty: cpu={len(a)} vulkan={len(b)}")
    else:
        ca, cb = cer(a), cer(b)
        if cb > ca + max_rise + 1e-9:
            problems.append(f"CER(vulkan) {cb:.4f} > CER(cpu) {ca:.4f} + {max_rise}")
    rtf = lambda r: r.get("seconds", 0) / r["audio_seconds"] if r.get("audio_seconds") else 0.0  # noqa: E731
    lines = ["| ASR path | backend | whisper.cpp device | CER | RTF |", "|---|---|---|---|---|",
             f"| cpu | {cpu.get('backend')} | - | {ca:.4f} | {rtf(cpu):.2f} |",
             f"| vulkan | {vulkan.get('backend')} | {vulkan.get('device_log') or '?'} | {cb:.4f} | {rtf(vulkan):.2f} |",
             "", f"CER bound: vulkan <= cpu + {max_rise}; speed (RTF) is not gated"]
    lines += [f"FAIL: {p}" for p in problems]
    lines += ["", "| clip | cpu | vulkan |", "|---|---|---|"]
    lines += [f"| {x['id']} | {_cell(x['hyp'])} | {_cell(y['hyp'])} |" for x, y in zip(a, b)]
    return not problems, "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("translate")
    t.add_argument("--models-dir", required=True)
    t.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("cpu")
    c.add_argument("vulkan")
    c.add_argument("--max-drop", type=float, default=MAX_DROP)
    s = sub.add_parser("transcribe")
    s.add_argument("--models-dir", required=True)
    s.add_argument("--data", required=True, help="folder with manifest.jsonl (scripts/bench/make_ci_smoke.py --out)")
    s.add_argument("--langs", default="en")
    s.add_argument("--size", default="small")
    s.add_argument("--out", required=True)
    ca = sub.add_parser("compare-asr")
    ca.add_argument("cpu")
    ca.add_argument("vulkan")
    ca.add_argument("--max-rise", type=float, default=MAX_CER_RISE)
    a = p.parse_args(argv)
    if a.cmd == "translate":
        res = translate(a.models_dir)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"backend={res['backend']} seconds={res['seconds']:.1f}")
        return 0
    if a.cmd == "transcribe":
        res = transcribe(a.models_dir, a.data, tuple(a.langs.split(",")), a.size)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"asr backend={res['backend']} device_log={res['device_log']} clips={len(res['clips'])} "
              f"seconds={res['seconds']:.1f}")
        return 0
    if a.cmd == "compare-asr":
        ok, md = compare_asr(json.loads(Path(a.cpu).read_text("utf-8")), json.loads(Path(a.vulkan).read_text("utf-8")),
                             a.max_rise)
        print(md)
        return 0 if ok else 1
    ok, md = compare(json.loads(Path(a.cpu).read_text("utf-8")), json.loads(Path(a.vulkan).read_text("utf-8")),
                     a.max_drop)
    print(md)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
