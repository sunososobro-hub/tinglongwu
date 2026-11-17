"""#1258: MT on cut-off ASR lines / idioms / split numbers, baseline vs (a) context vs (b) low-confidence mark."""
import json

from tinglongwu.hymt import HyMtTranslator, build_prompt, hymt_path
from tlwu_eval import fragments
from tlwu_eval.__main__ import main

SET = "eval/fragment_cases.jsonl"


class FakeLlm:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, **kw):
        self.calls.append(messages[0]["content"])
        return {"choices": [{"message": {"content": self.reply}}]}


def models(tmp_path):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    return tmp_path


def test_checked_in_set_has_50_items_of_every_kind():
    cases = fragments.load_cases(SET)
    assert len(cases) >= 50
    assert len({c["id"] for c in cases}) == len(cases)
    assert {c["kind"] for c in cases} == set(fragments.KINDS)
    assert {c["pair"] for c in cases} == {"ru->zh", "en->zh"}


def test_detector_marks_cut_off_lines_not_finished_ones():
    cut = ["и после этого модуль", "потому что антенна", "если сравнить с прошлой", "and after that the module",
           "if we compare it with the", "мы можем отправить в"]
    done = ["Модуль работает стабильно.", "Теперь всё работает", "Now everything works", "The module works fine."]
    assert all(fragments.incomplete(t, "ru" if t[0] > "z" else "en") for t in cut)
    assert not any(fragments.incomplete(t, "ru" if t[0] > "z" else "en") for t in done)
    assert not fragments.incomplete("и после этого модуль", "ja")  # unknown language: never marked


def test_invented_counts_completed_fragments_and_literal_traps():
    frag = {"kind": "fragment", "ref": "然後這之後模組"}
    assert not fragments.invented("然後模組", frag)
    assert fragments.invented("然後在這之後，模組就完全停止運作並且重新啟動了。", frag)
    idiom = {"kind": "idiom", "ref": "打開網頁入口網站。", "bad": ["傳送門"]}
    assert fragments.invented("打開網路傳送門。", idiom)
    assert not fragments.invented("打開網頁入口網站並登入很久很久很久很久很久。", idiom)  # length rule: fragments only


def fake_translate(text, src, tgt, context):
    """A model that smooths every cut-off line into an invented full sentence unless it has context."""
    if fragments.incomplete(text, src) and not context:
        return "然後這之後模組就完全停止運作並且重新啟動了。"
    return "然後這之後模組"


def test_run_reports_chrf_and_invented_per_condition():
    cases = [{"id": "f1", "kind": "fragment", "pair": "ru->zh", "prev": "Мы проверили прошивку.",
              "src": "и после этого модуль", "ref": "然後這之後模組"},
             {"id": "c1", "kind": "complete", "pair": "ru->zh", "prev": "x", "src": "Модуль работает.",
              "ref": "然後這之後模組"}]
    rep = fragments.run(cases, fake_translate)
    c = rep["conditions"]
    assert c["baseline"]["invented"] == 50.0 and c["baseline"]["invented_unmarked"] == 50.0
    assert c["context"]["invented"] == 0.0 and c["context"]["chrf"] == 100.0
    assert c["mark"]["invented"] == 50.0 and c["mark"]["invented_unmarked"] == 0.0
    assert rep["cases"][0]["out"]["mark"].endswith("…") and not rep["cases"][1]["out"]["mark"].endswith("…")
    assert rep["detector"] == {"cut_marked": "1/1", "complete_marked": "0/1"}
    assert "baseline | 2 |" in fragments.format_report(rep)


def test_build_prompt_context_form_and_default_unchanged():
    plain = build_prompt("и после этого модуль", "ru", "zh")
    assert build_prompt("и после этого модуль", "ru", "zh", context=None) == plain
    p = build_prompt("и после этого модуль", "ru", "zh", context="Мы проверили прошивку.")
    assert p.startswith("Мы проверили прошивку.\n") and p.endswith("\nи после этого модуль")
    assert "不需要翻译上文" in p


def test_hymt_translate_puts_context_in_prompt_only_when_given(tmp_path):
    fake = FakeLlm("然後模組")
    tr = HyMtTranslator(models(tmp_path), "tw", "cpu", loader=lambda p, g: fake)
    tr.translate("и после этого модуль", "ru", "zh")
    assert "Мы проверили" not in fake.calls[-1]
    tr.translate("и после этого модуль", "ru", "zh", context="Мы проверили прошивку.")
    assert fake.calls[-1].startswith("Мы проверили прошивку.")


def test_cli_writes_report(tmp_path, capsys):
    out = tmp_path / "frag.json"
    assert main(["--fragment-cases", SET, "--out", str(out)], translate=fake_translate) == 0
    rep = json.loads(out.read_text("utf-8"))
    assert set(rep["conditions"]) == {"baseline", "context", "mark"}
    assert rep["conditions"]["baseline"]["n"] >= 50
    assert "detector:" in capsys.readouterr().out
    assert main(["--fragment-cases", str(tmp_path / "missing.jsonl"), "--out", str(out)],
                translate=fake_translate) == 2


def test_set_has_20_ru_polysemy_items_with_wrong_sense_lists():
    """#1394: >= 20 self-written ru software-talk polysemy lines; `bad` holds the wrong everyday senses."""
    poly = [c for c in fragments.load_cases(SET) if c["kind"] == "polysemy"]
    assert len(poly) >= 20
    assert all(c["pair"] == "ru->zh" and c["bad"] and c["prev"].strip() for c in poly)
    assert not any(fragments.invented(c["ref"], c) for c in poly)  # a reference never trips its own bad list
    assert fragments.invented("刪除舊的樹枝。", next(c for c in poly if c["src"] == "Удали старую ветку."))


def test_run_reports_median_seconds_per_condition():
    ticks = iter([0.0, 1.0, 2.5, 10.0, 12.0, 15.0, 20.0, 21.0, 22.0])  # start, base done, ctx done
    cases = [{"id": f"p{i}", "kind": "polysemy", "pair": "ru->zh", "prev": "x", "src": "Удали старую ветку.",
              "ref": "刪除舊的分支。", "bad": ["樹枝"]} for i in range(3)]
    rep = fragments.run(cases, lambda t, s, g, c: "刪除舊的分支。", clock=lambda: next(ticks))
    assert rep["conditions"]["baseline"]["median_s"] == 1.0  # 1.0, 2.0, 1.0
    assert rep["conditions"]["context"]["median_s"] == 1.5  # 1.5, 3.0, 1.0
    assert rep["conditions"]["mark"]["median_s"] == 1.0
    assert "median s/line" in fragments.format_report(rep)
