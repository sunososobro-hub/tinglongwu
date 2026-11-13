"""Russian patches (see tinglongwu/lang/__init__.py).

#390 pre_mt: Russian person names are declined, and a declined male surname often looks like a female one
("Максима Чижова" = genitive of Максим Чижов, but "Чижова" is also the female nominative), so Hy-MT2 rendered
it as 馬克西姆·奇佐娃 instead of 奇若夫. Chinese has no case, so the MT input loses nothing when each name is put
back into the nominative of the person's actual gender: "Максима Чижова" -> "Максим Чижов".

Gender comes from the first name / patronymic next to the surname (Максима is only masculine). A lone surname is
rewritten only when its form has one gender (Ивановой = female, Сидоровым = male); an ambiguous lone form
("Иванова": male genitive or female nominative) is left as spoken, so female surnames never get masculinised.

pymorphy3 (MIT, with the MIT OpenCorpora-based Russian dictionary, ~13 MB) is imported on the first Russian
sentence only; without it the text passes through unchanged.
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?")
_PERSON = ("Name", "Surn", "Patr")
_MIN_SCORE = 0.3  # share of pymorphy's parse probability that must be a person name (Максима vs. "максима" the noun)
_GIVEN_SHARE = 0.75
_morph = None
_unavailable = False


def _analyzer():
    global _morph, _unavailable
    if _morph is None and not _unavailable:
        try:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer(lang="ru")
        except Exception:  # not installed / dictionary missing: the patch is off, MT still runs
            _unavailable = True
    return _morph


def _person_parses(morph, word: str) -> list:
    """Singular person-name readings of `word`, declinable ones first (Анне: Анна's dative before the Fixd "Анне")."""
    ps = [p for p in morph.parse(word) if "sing" in p.tag and any(t in p.tag for t in _PERSON)]
    return sorted(ps, key=lambda p: ("Fixd" in p.tag, -p.score))


def _strong(parses) -> bool:
    return sum(p.score for p in parses) >= _MIN_SCORE


def _genders(parses) -> set[str]:
    return {g for p in parses for g in ("masc", "femn") if g in p.tag}


def _given_gender(given) -> set[str]:
    """Gender of first name + patronymic: the one holding >= 75% of their parse probability (Дмитрию is 0.9
    "Дмитрий" vs 0.1 a rare female "Дмитрия"); empty when they disagree."""
    votes = {"masc": 0.0, "femn": 0.0}
    for ps in given:
        for p in ps:
            for g in votes:
                if g in p.tag:
                    votes[g] += p.score
    total = sum(votes.values())
    return {g for g, v in votes.items() if total and v / total >= _GIVEN_SHARE}


def _nominative(parses, gender: str) -> str | None:
    for p in parses:  # highest score first
        if gender in p.tag:
            if "nomn" in p.tag:
                return p.word
            q = p.inflect({"nomn", "sing", gender})
            if q is not None and gender in q.tag:
                return q.word
    return None


def _case_like(word: str, form: str) -> str:
    return "-".join(part[:1].upper() + part[1:] for part in form.split("-")) if word[:1].isupper() else form


def normalize_names(text: str) -> str:
    """Put declined person names into the nominative of the person's gender (see module docstring)."""
    morph = _analyzer()
    if morph is None or not _WORD.search(text):
        return text
    tokens = [(m, _person_parses(morph, m.group())) for m in _WORD.finditer(text)]
    runs, run = [], []
    for m, ps in tokens:  # runs of adjacent person-name words: "Максима Чижова", "Анной Сергеевной Петровой"
        if ps and run and text[run[-1][0].end():m.start()].strip() == "":
            run.append((m, ps))
        else:
            if run:
                runs.append(run)
            run = [(m, ps)] if ps else []
    if run:
        runs.append(run)
    edits = []
    # a weak reading (Чижовым: surname or adjective "чижовый") counts only next to a strong one (Максимом)
    for run in (r for r in runs if any(_strong(ps) for _m, ps in r)):
        given = [ps for _m, ps in run if any("Name" in p.tag or "Patr" in p.tag for p in ps)]
        genders = _given_gender(given) if given else _genders(run[0][1])
        if len(genders) != 1:
            continue  # unknown or ambiguous gender: leave as spoken
        gender = genders.pop()
        if not given and len(run) == 1 and "nomn" in max(run[0][1], key=lambda p: p.score).tag:
            continue  # lone surname whose best reading is already nominative
        for m, ps in run:
            form = _nominative(ps, gender)
            if form:
                edits.append((m.start(), m.end(), _case_like(m.group(), form)))
    for start, end, form in reversed(edits):
        text = text[:start] + form + text[end:]
    return text


def pre_mt(text: str) -> str:
    return normalize_names(text)
