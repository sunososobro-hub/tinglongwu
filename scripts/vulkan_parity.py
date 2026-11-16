"""#1195: does the Vulkan MT path give the same translations as the CPU path? (CI: Mesa lavapipe, a software GPU)

  TLWU_DEVICE=cpu    python scripts/vulkan_parity.py translate --models-dir D --out cpu.json
  TLWU_DEVICE=vulkan python scripts/vulkan_parity.py translate --models-dir D --out vulkan.json   (TLWU_VULKAN_PACK=<pack>)
  python scripts/vulkan_parity.py compare cpu.json vulkan.json [--max-drop 2]

`translate` loads Hy-MT2 the way a session does and records which backend really ran (mt_backend.last_backend) next to
the translations. `compare` exits 1 unless the CPU run said "cpu", the Vulkan run said "vulkan" (a silent fallback to
CPU must not pass) and chrF(Vulkan output, CPU output as reference) >= 100 - max_drop (the #1195 bound: chrF -2).
Speed is printed by `translate` but never gated.
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
MAX_DROP = 2.0  # chrF points, #1195 Acceptance


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
    return not problems, "\n".join(lines)


def translate(models_dir: str) -> dict:
    from tinglongwu import mt_backend
    from tinglongwu.selftest import _default_mt

    mt_backend.last_backend = ""
    t0 = time.perf_counter()
    tr = _default_mt(Path(models_dir), "hymt2", "tw", "cpu")
    texts = [(tr.translate(s, "en", "zh").text or "").strip() for s in SENTENCES]
    return {"backend": mt_backend.last_backend, "texts": texts, "seconds": time.perf_counter() - t0}


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
    a = p.parse_args(argv)
    if a.cmd == "translate":
        res = translate(a.models_dir)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"backend={res['backend']} seconds={res['seconds']:.1f}")
        return 0
    ok, md = compare(json.loads(Path(a.cpu).read_text("utf-8")), json.loads(Path(a.vulkan).read_text("utf-8")),
                     a.max_drop)
    print(md)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
