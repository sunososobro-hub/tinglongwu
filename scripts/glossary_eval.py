"""#535: RU -> ZH with vs without --glossary through the real Hy-MT2 path (JaRouter -> HyMtTranslator).

    python scripts/glossary_eval.py SRC.txt [--n 100] [--glossary FILE] [--auto-glossary] [--seed 0]
                                    [--engine hymt|hymt7b] [--models-dir DIR] [--device cuda|cpu] [--dump OUT.tsv]

SRC.txt: one Russian sentence per line (consecutive lines = running speech, so the 2-line glossary window applies).
Only lines with a Latin word or a digit are kept (that is what the retention metrics measure). --auto-glossary
builds `term = term` entries from the sample's own Latin words. Every run uses the same seed and temperature 0.
Metrics: English-term retention (Latin words found verbatim, case-insensitive, in the output), number retention
(source numbers found in the output, thousand separators ignored, decimal comma = point), residual-Russian
sentences (Cyrillic left in the output), repeat-loop sentences (tinglongwu.repeat.has_loop)."""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-.'][A-Za-z0-9]+)*")
NUMBER = re.compile(r"\d{1,3}(?:[  ]\d{3})+(?!\d)|\d+(?:[.,]\d+)?")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def numbers(text: str) -> list[str]:
    return [re.sub(r"[  ]", "", n).replace(",", ".") for n in NUMBER.findall(text)]


def out_numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)} | set(numbers(text))


def score(rows: list[tuple[str, str]]) -> dict:
    """rows: (source, output) -> the four metrics as raw counts, plus the sentences that lost a number."""
    lat = lat_ok = num = num_ok = rus = loops = 0
    lost = []
    from tinglongwu.repeat import has_loop

    for s, o in rows:
        words = LATIN.findall(s)
        lat += len(words)
        lat_ok += sum(w.lower() in o.lower() for w in words)
        ns, got = numbers(s), out_numbers(o)
        num += len(ns)
        miss = [n for n in ns if n not in got]
        num_ok += len(ns) - len(miss)
        if miss:
            lost.append((s, o, miss))
        rus += bool(CYRILLIC.search(o))
        loops += has_loop(o)
    pct = (lambda a, b: round(100.0 * a / b, 1) if b else None)
    return {"latin": f"{lat_ok}/{lat}", "latin_pct": pct(lat_ok, lat), "num": f"{num_ok}/{num}",
            "num_pct": pct(num_ok, num), "residual_ru": rus, "loops": loops, "n": len(rows), "lost": lost}


def run(lines: list[str], gl_path: str | None, args) -> list[tuple[str, str]]:
    from tinglongwu import cli, glossary
    from tinglongwu.engines import load_llama
    from tinglongwu.hymt import JaRouter

    def seeded(path, gpu):
        llm = load_llama(path, gpu)
        orig = llm.create_chat_completion
        llm.create_chat_completion = lambda messages, **kw: orig(messages, seed=args.seed, **kw)
        return llm

    import tinglongwu.hymt as hymt

    hymt._load_llama = seeded
    tr = JaRouter(None, Path(args.models_dir), "tw", args.device, all_pairs=True,
                  size="7b" if args.engine == "hymt7b" else "1.8b")
    if gl_path:
        cli.apply_glossary(tr, glossary.load(gl_path))
    return [(s, tr.translate(s, "ru", "zh").text) for s in lines]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--glossary")
    ap.add_argument("--auto-glossary", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--engine", default="hymt", choices=["hymt", "hymt7b"])
    ap.add_argument("--models-dir", default=str(Path.home() / "tinglongwu" / "models"))
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--dump")
    args = ap.parse_args(argv)
    os.environ["TLWU_HYMT_TEMP"] = "0"
    from tinglongwu.device import register_cuda_libs

    register_cuda_libs()
    src = [ln.strip() for ln in Path(args.src).read_text(encoding="utf-8").splitlines()]
    lines = [ln for ln in src if ln and (LATIN.search(ln) or re.search(r"\d", ln))][:args.n]
    gl = args.glossary
    if args.auto_glossary:
        terms = sorted({w for s in lines for w in LATIN.findall(s)}, key=str.lower)
        f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        f.write("".join(f"{t} = {t}\n" for t in terms))
        f.close()
        gl = f.name
    results = {}
    for name, g in (("without", None), ("with", gl)):
        rows = run(lines, g, args)
        results[name] = score(rows)
        if args.dump:
            with open(f"{args.dump}.{name}.tsv", "w", encoding="utf-8") as fh:
                fh.writelines(f"{s}\t{o}\n" for s, o in rows)
    print("| glossary | sentences | English-term retention | number retention | residual-Russian | repeat-loop |")
    print("|---|---|---|---|---|---|")
    for name, r in results.items():
        print(f"| {name} | {r['n']} | {r['latin_pct']}% ({r['latin']}) | {r['num_pct']}% ({r['num']}) | "
              f"{r['residual_ru']} | {r['loops']} |")
    for name, r in results.items():
        for s, o, miss in r["lost"]:
            print(f"lost number ({name}) {miss}: {s} => {o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
