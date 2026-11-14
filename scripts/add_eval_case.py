"""#679: turn a real `translation-error` report into a text eval case.

    python scripts/add_eval_case.py ISSUE_NUMBER [--repo OWNER/NAME] [--cases eval/user_cases.jsonl]
    python scripts/add_eval_case.py --issue-json issue.json [--cases ...]      # offline, from a saved issue
    python scripts/add_eval_case.py --label eval-case [--repo ...] [--cases ...]  # weekly loop: every closed issue

The issue is one opened with the "Translation error" form (.github/ISSUE_TEMPLATE/translation-error.yml). Its
"Language pair" field gives the pair, "What you expected" the reference, and "What happened" the source and the
tlwu output. In "What happened" the source is the line(s) after `Source:` / `原文:` and the output the line(s) after
`Output:` / `tlwu:` / `輸出:`; without those markers the first paragraph is the source and the second the output.

Each case is one JSON line in the cases file:
  {"id": "issue-679", "pair": "ja->zh", "src": "...", "ref": "...", "tlwu": "...", "target": "zh-TW",
   "source": "https://github.com/OWNER/NAME/issues/679"}
`pair` uses the eval gate's base codes (eval/baseline.json keys, e.g. ja->zh); `target` keeps the variant the user
asked for when it differs (zh-TW). A case whose `source` URL, or whose (pair, src) is already in the file is skipped.

Exit codes: 0 = appended or skipped as a duplicate, 2 = the issue cannot be turned into a case (message says why).
Issue text is data: it is only parsed, never executed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = "sunososobro-hub/tinglongwu"
DEFAULT_CASES = Path(__file__).resolve().parent.parent / "eval" / "user_cases.jsonl"
LABEL = "translation-error"  # the weekly loop takes closed issues the owner labelled `eval-case`

# Form field labels (prefix before " / ") -> key. GitHub renders each form field as "### <label>".
FIELDS = {"language pair": "pair", "what happened": "happened", "what you expected": "expected"}
SRC_MARK = re.compile(r"^\s*(?:source|src|original|原文|輸入)\s*[:：]\s*", re.IGNORECASE)
OUT_MARK = re.compile(r"^\s*(?:output|tlwu(?:\s+output)?|translation|輸出|翻譯|實際輸出)\s*[:：]\s*", re.IGNORECASE)
PAIR_SEP = re.compile(r"\s*(?:->|→|=>|>|⇒|\bto\b|/)\s*", re.IGNORECASE)
LANG_CODE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z0-9]{2,4})?$", re.IGNORECASE)
NO_RESPONSE = "_no response_"
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")  # CommonMark code fence: a closing fence matches the opener


class CaseError(ValueError):
    """The issue does not hold a usable case."""


def form_fields(body: str) -> dict[str, str]:
    """Issue-form body -> {pair, happened, expected} (only the fields we know; empty / '_No response_' dropped)."""
    out, key, buf, fenced = {}, None, [], False

    def flush():
        if key:
            text = "\n".join(buf).strip()
            if text and text.lower() != NO_RESPONSE:
                out[key] = text

    for line in (body or "").replace("\r\n", "\n").split("\n"):
        f = FENCE.match(line)
        if f and not fenced:
            fenced = f.group(1)
        elif f and fenced and f.group(1)[0] == fenced[0] and len(f.group(1)) >= len(fenced) and not f.group(2).strip():
            fenced = False
        m = None if fenced else re.match(r"^#{2,4}\s+(.+?)\s*$", line)
        if m:
            flush()
            label = m.group(1).split(" / ")[0].strip().lower()
            label = re.sub(r"\s*\(.*\)$", "", label)
            key, buf = FIELDS.get(label), []
            continue
        if key:
            buf.append(line)
    flush()
    return out


def _unfence(text: str) -> str:
    lines = [ln for ln in text.strip().split("\n") if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def parse_pair(text: str) -> tuple[str, str, str]:
    """'ja -> zh-TW' -> ('ja', 'zh', 'zh-TW'): source base code, target base code, target as written."""
    parts = [p for p in PAIR_SEP.split(text.strip().splitlines()[0].strip()) if p]
    if len(parts) != 2 or not all(LANG_CODE.match(p) for p in parts):
        raise CaseError(f"language pair {text!r} is not 'src -> tgt' with language codes (e.g. ja -> zh-TW)")
    src, tgt = parts[0].replace("_", "-"), parts[1].replace("_", "-")
    head, _, tail = tgt.partition("-")
    variant = head.lower() + (f"-{tail.upper() if len(tail) == 2 else tail.title()}" if tail else "")
    return src.split("-")[0].lower(), head.lower(), variant


def split_happened(text: str) -> tuple[str, str]:
    """'What happened' -> (source text, tlwu output). Marked lines win; else paragraph 1 / paragraph 2."""
    text = _unfence(text)
    src, out, cur, marked = [], [], None, False
    for line in text.split("\n"):
        if SRC_MARK.match(line):
            cur, marked = src, True
            line = SRC_MARK.sub("", line)
        elif OUT_MARK.match(line):
            cur, marked = out, True
            line = OUT_MARK.sub("", line)
        if cur is not None:
            cur.append(line)
    if marked:
        source = "\n".join(src).strip()
        if not source:
            raise CaseError("'What happened' has an output marker but no 'Source:' text")
        return source, "\n".join(out).strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) == 2:
        return paras[0], paras[1]
    if len(paras) == 1 and len(paras[0].split("\n")) == 2:
        a, b = paras[0].split("\n")
        return a.strip(), b.strip()
    raise CaseError("'What happened' must hold the source and the tlwu output: add a 'Source:' line and an "
                    "'Output:' line (or two paragraphs: source first, output second)")


def case_from_issue(issue: dict) -> dict:
    if "pull_request" in issue:
        raise CaseError(f"#{issue.get('number')} is a pull request, not an issue")
    f = form_fields(issue.get("body") or "")
    missing = [k for k in ("pair", "happened", "expected") if k not in f]
    if missing:
        raise CaseError(f"#{issue.get('number')}: missing form field(s) {', '.join(missing)} "
                        "(open the issue with the Translation error form)")
    src_lang, tgt_lang, target = parse_pair(f["pair"])
    src, tlwu = split_happened(f["happened"])
    ref = _unfence(f["expected"])
    if not src or not ref:
        raise CaseError(f"#{issue.get('number')}: empty source or expected translation")
    case = {"id": f"issue-{issue['number']}", "pair": f"{src_lang}->{tgt_lang}", "src": src, "ref": ref}
    if tlwu:
        case["tlwu"] = tlwu
    if target != tgt_lang:
        case["target"] = target
    case["source"] = issue["html_url"]
    return case


def load_cases(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text("utf-8").split("\n") if ln.strip()]


def append_case(path: Path, case: dict) -> bool:
    """Append `case` unless the same issue URL or the same (pair, src) is already there. True = appended."""
    for c in load_cases(path):
        if c.get("source") == case["source"] or (c.get("pair"), c.get("src")) == (case["pair"], case["src"]):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.is_file() and path.stat().st_size:
        prefix = "" if path.read_bytes().endswith(b"\n") else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(prefix + json.dumps(case, ensure_ascii=False) + "\n")
    return True


def gh_json(endpoint: str, run=subprocess.run):
    r = run(["gh", "api", endpoint], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise CaseError(f"gh api {endpoint} failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def labelled_issues(repo: str, label: str, run=subprocess.run) -> list[dict]:
    """Closed issues with `label` (oldest first), every page."""
    out, page = [], 1
    while True:
        batch = gh_json(f"repos/{repo}/issues?state=closed&labels={label}&per_page=100&page={page}", run)
        out += [i for i in batch if "pull_request" not in i]
        if len(batch) < 100:
            return sorted(out, key=lambda i: i["number"])
        page += 1


def add(issue: dict, cases: Path) -> str:
    case = case_from_issue(issue)
    if append_case(cases, case):
        return f"added {case['id']} ({case['pair']}) -> {cases}"
    return f"skipped {case['id']}: already in {cases}"


def main(argv=None, run=subprocess.run) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("issue", nargs="?", type=int, help="issue number of a translation-error report")
    g.add_argument("--issue-json", type=Path, help="a saved issue (GitHub REST JSON) instead of fetching one")
    g.add_argument("--label", help="add every closed issue with this label (the weekly loop uses eval-case)")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    a = p.parse_args(argv)
    try:
        if a.label:
            issues = labelled_issues(a.repo, a.label, run)
        elif a.issue_json:
            issues = [json.loads(a.issue_json.read_text("utf-8"))]
        else:
            issues = [gh_json(f"repos/{a.repo}/issues/{a.issue}", run)]
    except (CaseError, OSError, json.JSONDecodeError) as e:
        print(f"add_eval_case: {e}", file=sys.stderr)
        return 2
    code = 0
    for issue in issues:
        try:
            print(add(issue, a.cases))
        except CaseError as e:
            print(f"add_eval_case: {e}", file=sys.stderr)
            code = 2
    if a.label:
        print(f"{len(issues)} issue(s) with label {a.label!r} checked")
    return code


if __name__ == "__main__":
    sys.exit(main())
