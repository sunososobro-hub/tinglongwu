"""#1258: MT on truncated ASR fragments, idioms and split numbers, in three conditions.

`python -m tlwu_eval --fragment-cases eval/fragment_cases.jsonl [--out fragments.json]` translates every case with
Hy-MT2 (the listen-path engine) and reports chrF and an invented-content rate for:
  baseline  the line alone (what tlwu does today)
  context   (a) the previous source line goes into the prompt (Hy-MT2 card's contextual form, `hymt.build_prompt`)
  mark      (b) lines that look cut off (`incomplete`) keep the baseline text but end in "…" instead of a full stop,
            so the reader sees an unfinished line instead of a fluent, smoothed-over sentence
Case kinds: fragment (ASR cut mid-sentence), idiom (literal-translation traps), number (number/unit split over two
lines), complete (finished sentences; the `mark` detector must leave them alone).

Invented content (per output): it contains one of the case's `bad` strings (known literal / invented renderings,
e.g. 傳送門 for "web portal"), or, for fragment / number cases, it is much longer than the reference
(> INVENT_RATIO x and > INVENT_MIN_EXTRA content characters more): the fragment was completed into a sentence the
speaker never said. `invented` counts every such output; `invented_unmarked` only those not marked with "…" (those
are shown to the reader as finished sentences). Informational eval, not a gate."""
from __future__ import annotations

import json
import re
from collections import OrderedDict

from . import scoring

REQUIRED = ("id", "kind", "pair", "prev", "src", "ref")
KINDS = ("fragment", "idiom", "number", "complete")
CONDITIONS = ("baseline", "context", "mark")
MARK = "…"
INVENT_RATIO = 1.5  # output content chars > 1.5 x the reference's ...
INVENT_MIN_EXTRA = 4  # ... and at least 4 more: a 3-char reference may still come back as 5 chars of the same words
_END = ".!?。！？…"
_CONTENT = re.compile(r"[\w㐀-鿿]", re.UNICODE)
_WORDS = re.compile(r"[\w'-]+", re.UNICODE)

# Words after which a line cannot end (conjunctions, prepositions, articles, number words) and words a cut-off
# continuation starts with. Lower case; ru and en are the #1258 meeting languages.
TAIL = {
    "ru": set("и а но или что чтобы если как в во на с со к ко по из от до за для при без о об у через после перед "
              "тем том это не бы ли же который которая которые когда где потому так то его её их наш ваш "
              "из-за один два двух три трёх четыре пять шесть семь восемь девять десять двадцать тридцать сорок "
              "сто ста двести тысяча тысячи тысяч".split()),
    "en": set("and or but the a an to of in on at for with from by that which if because so than then is are was "
              "were be its their our my your his her about as into when where while hundred thousand one two three "
              "four five six seven eight nine ten twenty thirty forty".split()),
}
HEAD = {
    "ru": set("и а но или потому если при часть кто там тогда особенно возможно".split()),
    "en": set("and or but because if so maybe one some they".split()),
}


def load_cases(path) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as e:
                raise ValueError(f"{path}:{n}: not valid JSON ({e})") from e
            missing = [k for k in REQUIRED if not isinstance(row.get(k), str) or not row[k].strip()]
            if missing:
                raise ValueError(f"{path}:{n}: missing {', '.join(missing)}")
            if row["kind"] not in KINDS:
                raise ValueError(f"{path}:{n}: kind {row['kind']!r} not in {KINDS}")
            if "->" not in row["pair"]:
                raise ValueError(f"{path}:{n}: pair {row['pair']!r} is not 'src->tgt'")
            cases.append(row)
    return cases


def incomplete(text: str, lang: str) -> bool:
    """Does this ASR line look cut off? No sentence-final punctuation, and it ends on a word a sentence cannot end
    on, or starts like a continuation (lower-case conjunction etc.). Unknown languages: never."""
    t = text.strip()
    if not t or lang not in TAIL:
        return False
    if t.endswith(("…", "...", "-", "—")):
        return True
    if t[-1] in _END:
        return False
    words = [w.lower() for w in _WORDS.findall(t)]
    if not words:
        return False
    if words[-1] in TAIL[lang]:
        return True
    return t[0].islower() and words[0] in HEAD[lang]


def mark(out: str) -> str:
    """Show a translation as unfinished: drop its final full stop and end it in "…"."""
    return out.rstrip().rstrip(_END).rstrip() + MARK


def content_len(s: str) -> int:
    return len(_CONTENT.findall(s))


def invented(out: str, case: dict) -> bool:
    text = out.replace(MARK, "")
    if any(b in text for b in case.get("bad", ())):
        return True
    if case["kind"] in ("fragment", "number"):
        n, r = content_len(text), content_len(case["ref"])
        return n > INVENT_RATIO * r and n - r >= INVENT_MIN_EXTRA
    return False


def run(cases, translate) -> dict:
    """translate(text, src, tgt, context) -> str (context None = the line alone). Returns per-condition totals,
    per-kind totals and per-case rows."""
    rows = []
    for c in cases:
        src, tgt = (x.strip() for x in c["pair"].split("->", 1))
        base = translate(c["src"], src, tgt, None).strip()
        ctx = translate(c["src"], src, tgt, c["prev"]).strip()
        flagged = incomplete(c["src"], src)
        outs = {"baseline": base, "context": ctx, "mark": mark(base) if flagged else base}
        row = {"id": c["id"], "kind": c["kind"], "pair": c["pair"], "flagged": flagged, "out": outs}
        for k, o in outs.items():
            row[k] = {"chrf": round(scoring.chrf([(c["ref"], o.replace(MARK, ""))]), 1),
                      "invented": invented(o, c), "marked": o.endswith(MARK)}
        rows.append(row)
    return {"conditions": _totals(rows, cases), "kinds": _by_kind(rows, cases), "detector": _detector(rows),
            "cases": rows}


def _agg(rows, cases_by_id, cond) -> dict:
    n = len(rows)
    pairs = [(cases_by_id[r["id"]]["ref"], r["out"][cond].replace(MARK, "")) for r in rows]
    inv = sum(r[cond]["invented"] for r in rows)
    inv_un = sum(r[cond]["invented"] and not r[cond]["marked"] for r in rows)
    return {"n": n, "chrf": round(scoring.chrf(pairs), 1) if n else 0.0,
            "invented": round(100 * inv / n, 1) if n else 0.0,
            "invented_unmarked": round(100 * inv_un / n, 1) if n else 0.0}


def _totals(rows, cases) -> dict:
    by_id = {c["id"]: c for c in cases}
    return {cond: _agg(rows, by_id, cond) for cond in CONDITIONS}


def _by_kind(rows, cases) -> dict:
    by_id = {c["id"]: c for c in cases}
    groups = OrderedDict((k, [r for r in rows if r["kind"] == k]) for k in KINDS)
    return {k: {cond: _agg(g, by_id, cond) for cond in CONDITIONS} for k, g in groups.items() if g}


def _detector(rows) -> dict:
    """How well `incomplete` separates lines cut at the end (fragment) from finished ones (complete). Number cases
    are continuation lines (cut at the start, finished at the end), so they are not counted either way."""
    cut = [r for r in rows if r["kind"] == "fragment"]
    done = [r for r in rows if r["kind"] == "complete"]
    return {"cut_marked": f"{sum(r['flagged'] for r in cut)}/{len(cut)}",
            "complete_marked": f"{sum(r['flagged'] for r in done)}/{len(done)}"}


def format_report(rep: dict) -> str:
    lines = ["condition | n | chrF | invented % | invented unmarked %", "---|---|---|---|---"]
    for cond, t in rep["conditions"].items():
        lines.append(f"{cond} | {t['n']} | {t['chrf']} | {t['invented']} | {t['invented_unmarked']}")
    lines += ["", "kind | condition | n | chrF | invented % | invented unmarked %", "---|---|---|---|---|---"]
    for kind, conds in rep["kinds"].items():
        for cond, t in conds.items():
            lines.append(f"{kind} | {cond} | {t['n']} | {t['chrf']} | {t['invented']} | {t['invented_unmarked']}")
    d = rep["detector"]
    lines += ["", f"detector: cut-off lines marked {d['cut_marked']}, complete lines marked {d['complete_marked']}"]
    return "\n".join(lines)
