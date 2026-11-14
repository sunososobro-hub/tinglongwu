"""#679: scripts/add_eval_case.py turns a translation-error issue into one text eval case, skips duplicates."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import add_eval_case as aec

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "add_eval_case.py"
URL = "https://github.com/sunososobro-hub/tinglongwu/issues/"


def form_body(pair="ja -> zh-TW", happened="Source: 会議は明日の午後三時からです。\nOutput: 會議是明天下午。",
              expected="會議從明天下午三點開始。"):
    """The body GitHub renders for .github/ISSUE_TEMPLATE/translation-error.yml."""
    return (f"### Version / 版本\n\nv0.3.4\n\n### OS / 作業系統\n\nWindows\n\n### Mode / 模式\n\nlisten\n\n"
            f"### Language pair / 語言組合\n\n{pair}\n\n"
            f"### What happened (source text and what tlwu output) / 原文與實際輸出\n\n{happened}\n\n"
            f"### What you expected (correct translation) / 正確翻譯\n\n{expected}\n\n"
            "### Log lines (optional) / 日誌（選填）\n\n_No response_\n")


def issue(n=900, **kw):
    return {"number": n, "html_url": f"{URL}{n}", "title": "wrong time", "body": form_body(**kw),
            "labels": [{"name": "translation-error"}]}


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", check=False)


def test_fixture_issue_appends_exactly_one_case(tmp_path):
    fixture, cases = tmp_path / "issue.json", tmp_path / "eval" / "user_cases.jsonl"
    fixture.write_text(json.dumps(issue(), ensure_ascii=False), "utf-8")
    r = run_cli("--issue-json", str(fixture), "--cases", str(cases))
    assert r.returncode == 0, r.stderr
    assert "added issue-900 (ja->zh)" in r.stdout
    rows = [json.loads(ln) for ln in cases.read_text("utf-8").splitlines()]
    assert rows == [{"id": "issue-900", "pair": "ja->zh", "src": "会議は明日の午後三時からです。",
                     "ref": "會議從明天下午三點開始。", "tlwu": "會議是明天下午。", "target": "zh-TW",
                     "source": f"{URL}900"}]


def test_duplicates_are_skipped(tmp_path):
    fixture, cases = tmp_path / "issue.json", tmp_path / "user_cases.jsonl"
    fixture.write_text(json.dumps(issue()), "utf-8")
    assert run_cli("--issue-json", str(fixture), "--cases", str(cases)).returncode == 0
    r = run_cli("--issue-json", str(fixture), "--cases", str(cases))
    assert r.returncode == 0 and "skipped issue-900" in r.stdout
    assert len(cases.read_text("utf-8").splitlines()) == 1
    # a second report of the same sentence (other issue number) is a duplicate too
    assert aec.append_case(cases, aec.case_from_issue(issue(n=901))) is False
    assert len(cases.read_text("utf-8").splitlines()) == 1
    # a different sentence is appended after the existing line
    assert aec.append_case(cases, aec.case_from_issue(issue(n=902, happened="Source: はい\nOutput: 不"))) is True
    assert [json.loads(ln)["id"] for ln in cases.read_text("utf-8").splitlines()] == ["issue-900", "issue-902"]


def test_appends_after_a_file_without_final_newline(tmp_path):
    cases = tmp_path / "user_cases.jsonl"
    cases.write_text('{"id": "old", "pair": "en->zh", "src": "a", "ref": "b", "source": "x"}', "utf-8")
    assert aec.append_case(cases, aec.case_from_issue(issue())) is True
    assert [json.loads(ln)["id"] for ln in cases.read_text("utf-8").splitlines()] == ["old", "issue-900"]


@pytest.mark.parametrize("pair,expected", [
    ("ja -> zh-TW", ("ja", "zh", "zh-TW")), ("en→zh", ("en", "zh", "zh")), ("RU > ZH", ("ru", "zh", "zh")),
    ("zh to en", ("zh", "en", "en")), ("ja/zh_tw", ("ja", "zh", "zh-TW")), ("zh-Hant -> en", ("zh", "en", "en")),
])
def test_language_pair_forms(pair, expected):
    assert aec.parse_pair(pair) == expected


def test_unmarked_source_and_output_as_two_paragraphs():
    case = aec.case_from_issue(issue(pair="en -> zh", happened="```\nThe meeting starts at 3.\n\n會議開始。\n```",
                                     expected="會議三點開始。"))
    assert (case["pair"], case["src"], case["tlwu"], case["ref"]) == ("en->zh", "The meeting starts at 3.",
                                                                       "會議開始。", "會議三點開始。")
    assert "target" not in case


@pytest.mark.parametrize("kw,msg", [
    ({"pair": "Japanese"}, "language pair"),
    ({"happened": "one blob of text without a split"}, "'What happened'"),
    ({"expected": "_No response_"}, "missing form field(s) expected"),
])
def test_unusable_issue_exits_2_and_appends_nothing(tmp_path, kw, msg):
    fixture, cases = tmp_path / "issue.json", tmp_path / "user_cases.jsonl"
    fixture.write_text(json.dumps(issue(**kw)), "utf-8")
    r = run_cli("--issue-json", str(fixture), "--cases", str(cases))
    assert r.returncode == 2 and msg in r.stderr
    assert not cases.exists()


def test_weekly_label_loop_adds_each_closed_issue_once(tmp_path):
    pages = {1: [issue(n=905, happened="Source: いいえ\nOutput: 是"), {"number": 906, "pull_request": {}},
                 issue(n=903)]}
    calls = []

    def fake_run(cmd, **_):
        calls.append(cmd[-1])
        page = int(cmd[-1].rsplit("page=", 1)[1])
        return subprocess.CompletedProcess(cmd, 0, json.dumps(pages.get(page, [])), "")

    cases = tmp_path / "user_cases.jsonl"
    assert aec.main(["--label", "eval-case", "--cases", str(cases)], run=fake_run) == 0
    assert calls == ["repos/sunososobro-hub/tinglongwu/issues?state=closed&labels=eval-case&per_page=100&page=1"]
    assert [json.loads(ln)["id"] for ln in cases.read_text("utf-8").splitlines()] == ["issue-903", "issue-905"]
    assert aec.main(["--label", "eval-case", "--cases", str(cases)], run=fake_run) == 0  # next week: nothing new
    assert len(cases.read_text("utf-8").splitlines()) == 2


def test_issue_number_fetches_with_gh(tmp_path):
    def fake_run(cmd, **_):
        assert cmd == ["gh", "api", "repos/o/r/issues/900"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(issue()), "")

    cases = tmp_path / "user_cases.jsonl"
    assert aec.main(["900", "--repo", "o/r", "--cases", str(cases)], run=fake_run) == 0
    assert json.loads(cases.read_text("utf-8"))["source"] == f"{URL}900"


def test_heading_inside_fenced_expected_is_kept():
    expected = "前言\n```\n### 標題\n正文\n```"
    case = aec.case_from_issue(issue(expected=expected))
    assert case["ref"] == "前言\n### 標題\n正文"


@pytest.mark.parametrize("happened", ["Output: first line\nsecond line", "Source:\nOutput: 是"])
def test_output_marker_without_source_is_rejected(happened):
    with pytest.raises(aec.CaseError, match="no 'Source:' text"):
        aec.case_from_issue(issue(happened=happened))


def test_unicode_line_separator_in_a_case_survives_later_appends(tmp_path):
    cases = tmp_path / "user_cases.jsonl"
    assert aec.append_case(cases, aec.case_from_issue(issue(expected="會議 三點開始。"))) is True
    assert aec.append_case(cases, aec.case_from_issue(issue())) is False  # duplicate check still parses the file
    assert aec.append_case(cases, aec.case_from_issue(issue(n=910, happened="Source: はい\nOutput: 不"))) is True
    rows = aec.load_cases(cases)
    assert [r["id"] for r in rows] == ["issue-900", "issue-910"] and rows[0]["ref"] == "會議 三點開始。"


def test_shorter_fence_inside_a_longer_fence_does_not_close_it():
    happened = "Source: hello\nOutput:\n````\n```\nwrong\n````"
    case = aec.case_from_issue(issue(pair="en -> zh", happened=happened, expected="你好"))
    assert case["src"] == "hello" and case["ref"] == "你好"
