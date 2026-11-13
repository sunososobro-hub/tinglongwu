"""#472 (#335 part 3): COMET x100 per language against a sanity floor that decides Experimental vs supported.

The floor was stated in #472 before any run: a language whose COMET x100 is >= 75.00 may leave
`tinglongwu.langs.EXPERIMENTAL`; below it (or with no score) it stays Experimental. The eval only prints the
verdict; moving a language out of the list is a reviewed code change made with the pasted table.
"""
from __future__ import annotations

COMET_FLOOR = 75.00  # #472 issue text: "This issue sets that floor at 75.00"


def verdicts(comet: dict, pairs) -> list[dict]:
    """One row per requested pair: COMET x100 (or None) and 'supported' / 'stays Experimental'.
    comet: unrounded x100 scores, so 74.996 stays below the floor; the row keeps 2 decimals for display."""
    rows = []
    for src, tgt in pairs:
        pair = f"{src}->{tgt}"
        score = comet.get(pair)
        ok = score is not None and score >= COMET_FLOOR
        rows.append({"pair": pair, "comet": None if score is None else round(score, 2), "floor": COMET_FLOOR,
                     "verdict": "supported" if ok else "stays Experimental"})
    return rows


def table(rows: list[dict]) -> str:
    """Markdown table to paste into the PR."""
    out = [f"| pair | COMET x100 | floor {COMET_FLOOR:.2f} |", "|---|---|---|"]
    for r in rows:
        score = "n/a (no clips with ref_zh)" if r["comet"] is None else f"{r['comet']:.2f}"
        out.append(f"| {r['pair']} | {score} | {r['verdict']} |")
    return "\n".join(out)
