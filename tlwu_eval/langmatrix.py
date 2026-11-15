"""#642 (#619 part 2): per-language cloud eval matrix from one config file, eval/languages.json.

  python -m tlwu_eval.langmatrix matrix                      # GitHub Actions matrix JSON (pairs whose pins exist)
  python -m tlwu_eval.langmatrix baseline en->zh --out b.json  # one-pair baseline for `tlwu_eval --gate`
  python -m tlwu_eval.langmatrix summary r.json en->zh        # ASR error rate / chrF line for the job summary

Add a language = one line in eval/languages.json: {"pair": "xx->zh", "pins": "eval/<set>_pins.json", "clips": 20}.
Each pair is gated against its eval/baseline.json "matrix" entry (chrF - margin, MT median budget, tlwu_eval/gate.py;
"chrf": null = not recorded yet, latency only). A pair without an entry, or whose clips have no reference translation
in its target language, is red.
A pair whose pin file is not committed yet is left out of the matrix and listed as `unpinned`."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from tlwu_eval import gate

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "eval" / "languages.json"
BASELINE = ROOT / "eval" / "baseline.json"
PAIR_RE = re.compile(r"^[a-z]{2,3}->[a-z]{2,3}$")


class ConfigError(ValueError):
    """eval/languages.json is unreadable or invalid (exit 2)."""


def load_config(path=CONFIG) -> list[dict]:
    """Validated pair entries [{pair, src, tgt, pins, clips}] in file order."""
    try:
        doc = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path}: {e}") from None
    pairs = doc.get("pairs") if isinstance(doc, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise ConfigError(f"{path}: 'pairs' must be a non-empty list")
    out, seen = [], set()
    for i, e in enumerate(pairs):
        where = f"{path}: pairs[{i}]"
        if not isinstance(e, dict):
            raise ConfigError(f"{where} must be an object")
        pair = e.get("pair")
        if not isinstance(pair, str) or not PAIR_RE.match(pair):
            raise ConfigError(f"{where}: 'pair' must look like 'en->zh', got {pair!r}")
        src, tgt = pair.split("->")
        if src == tgt:
            raise ConfigError(f"{where}: source and target are the same ({pair})")
        if pair in seen:
            raise ConfigError(f"{where}: {pair} is listed twice")
        seen.add(pair)
        pins = e.get("pins")
        if not isinstance(pins, str) or not pins.startswith("eval/") or ".." in pins or not pins.endswith(".json"):
            raise ConfigError(f"{where}: 'pins' must be a pin file under eval/, got {pins!r}")
        clips = e.get("clips")
        if not isinstance(clips, int) or isinstance(clips, bool) or not 1 <= clips <= 50:
            raise ConfigError(f"{where}: 'clips' must be an integer 1..50")
        out.append({"pair": pair, "src": src, "tgt": tgt, "pins": pins, "clips": clips})
    return out


def pinned_clips(pins: dict, src: str) -> int:
    return sum(1 for c in pins.get("clips", []) if isinstance(c, dict) and c.get("lang") == src)


def matrix(entries: list[dict], root=ROOT) -> tuple[list[dict], list[str]]:
    """(jobs, unpinned): one job per pair whose pin file exists and has clips of the source language.
    A job runs min(clips, pinned) clips; `data` is the folder make_ci_smoke.py builds from that pin file."""
    jobs, unpinned = [], []
    for e in entries:
        p = Path(root) / e["pins"]
        try:
            pins = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            unpinned.append(e["pair"])
            continue
        n = pinned_clips(pins, e["src"])
        if n == 0:
            unpinned.append(e["pair"])
            continue
        jobs.append({**e, "clips": min(e["clips"], n), "data": "eval_data/" + Path(e["pins"]).stem})
    return jobs, unpinned


def pair_baseline(pair: str, baseline: dict) -> dict:
    """A one-pair baseline document for gate.run(). A pair with no eval/baseline.json "matrix" entry is a setup error:
    a changed target language (en->zh edited to en->ja) must turn that job red, not pass on latency alone."""
    meta = baseline.get("matrix")
    rows = meta.get("pairs") if isinstance(meta, dict) else None
    if not isinstance(rows, dict) or pair not in rows:
        raise gate.GateError(f"eval/baseline.json has no matrix entry for {pair}: add one (chrf null until a run is recorded)")
    doc = {"model": meta.get("model", baseline["model"]), "engine": meta.get("engine", baseline["engine"]),
           "clips": meta.get("clips", baseline["clips"]), "pairs": {pair: rows[pair]}}
    return gate.validate_baseline(doc)


def summary_line(report: dict, pair: str) -> tuple[bool, str]:
    """(ok, line). Not ok when the pair has no clips or no chrF: no reference translation in the target language
    means the pair points at a target the pinned clips do not have."""
    lg = next((x for x in report.get("languages", []) if isinstance(x, dict) and x.get("pair") == pair), None)
    if lg is None or not lg.get("clips"):
        return False, f"{pair}: FAIL (no clips evaluated)"
    er, chrf = lg.get("error_rate"), lg.get("chrf")
    er_s = "n/a" if er is None else f"{er * 100:.2f}%"
    line = f"{pair}: {len(lg['clips'])} clips, ASR error rate {er_s}, chrF " + ("n/a" if chrf is None else f"{chrf:.2f}")
    if chrf is None:
        return False, line + f" - FAIL (no ref_{pair.split('->')[1]} reference translations in the clips)"
    return True, line


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m tlwu_eval.langmatrix", description=__doc__.split("\n")[0])
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--baseline", default=str(BASELINE))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("matrix")
    b = sub.add_parser("baseline")
    b.add_argument("pair")
    b.add_argument("--out", required=True)
    s = sub.add_parser("summary")
    s.add_argument("report")
    s.add_argument("pair")
    a = p.parse_args(argv)
    try:
        if a.cmd == "matrix":
            jobs, unpinned = matrix(load_config(a.config), Path(a.config).resolve().parents[1])
            print(json.dumps({"include": jobs, "unpinned": unpinned}, separators=(",", ":")))
            return 0 if jobs else 1
        if a.cmd == "baseline":
            doc = pair_baseline(a.pair, gate.validate_baseline(gate.load_json(a.baseline, "baseline")))
            Path(a.out).write_text(json.dumps(doc, indent=2), "utf-8")
            return 0
        ok, line = summary_line(gate.load_json(a.report, "report"), a.pair)
        print(line)
        return 0 if ok else 1
    except (ConfigError, gate.GateError) as e:
        print(f"langmatrix: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
