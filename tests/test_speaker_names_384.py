"""#384: rename / relabel / merge speakers, the one-line console label, --save and the eval adapter."""
import io
import json
import subprocess
import sys
from pathlib import Path

from tinglongwu import fmt
from tinglongwu.sink import JsonlSaveSink, Result, TextSink
from tinglongwu.speakers import SpeakerBook

EVAL = Path(__file__).resolve().parents[1] / "scripts" / "eval_diarization.py"


SPK = ["說話者1", "說話者2", "說話者1", "說話者3", "說話者2"]
RESULTS = [Result("ru", f"line {i}", None, f"行 {i}", "zh", speaker=spk) for i, spk in enumerate(SPK)]


def _book():
    book = SpeakerBook()
    [book.add(r.speaker) for r in RESULTS]
    return book


def test_rename_updates_past_and_future_lines():
    book = _book()
    book.rename("說話者2", "Boss")
    assert [book.display(book.corrected(i)) for i in range(5)] == ["說話者1", "Boss", "說話者1", "說話者3", "Boss"]
    book.add("說話者2")  # a later line of the same speaker
    assert book.display(book.corrected(5)) == "Boss"
    book.rename("說話者2", "  ")  # empty name: back to the label
    assert book.display("說話者2") == "說話者2"


def test_relabel_changes_one_line_only():
    book = _book()
    book.relabel(2, "說話者2")
    assert [book.corrected(i) for i in range(5)] == ["說話者1", "說話者2", "說話者2", "說話者3", "說話者2"]
    new = book.new_label()
    assert new == "說話者4"
    book.relabel(0, new)
    assert book.corrected(0) == "說話者4" and book.corrected(2) == "說話者2"
    assert book.lines[0][0] == "說話者1"  # the auto label is kept for scoring


def test_merge_two_speakers_keeps_names():
    book = _book()
    book.rename("說話者3", "Anna")
    book.merge("說話者3", "說話者1")
    assert [book.corrected(i) for i in range(5)] == ["說話者1", "說話者2", "說話者1", "說話者1", "說話者2"]
    assert book.display("說話者3") == "Anna" and book.display("說話者1") == "Anna"
    book.merge("說話者1", "說話者3")  # merged into itself: no loop
    assert book.resolve("說話者3") == "說話者1"


def test_console_line_shows_speaker_instead_of_them():
    out = io.StringIO()
    book = SpeakerBook()
    book.rename("說話者3", "Boss")
    sink = TextSink(out, names=book)
    sink.write(Result("ru", "привет", None, "你好", "zh", speaker="說話者2"))
    sink.write_source("ru", "пока", speaker="說話者3")
    sink.write(Result("ru", "пока", None, "再見", "zh", speaker="說話者3"))
    sink.write(Result("ru", "да", None, "是", "zh"))  # diarization off / unknown
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    assert lines[0].endswith("◀ 說話者2 привет")
    assert lines[2].endswith("◀ Boss пока")
    assert lines[4].endswith(fmt.t("label_them") + " да")
    assert sum("說話者" in ln or "Boss" in ln for ln in lines) == 2  # one line each, no extra speaker line


def test_save_keeps_speaker_and_fixes_and_eval_scores_them(tmp_path):
    path = tmp_path / "s.jsonl"
    save = JsonlSaveSink(TextSink(io.StringIO()), path)
    [save.write(r) for r in RESULTS]
    save.write(Result("ru", "?", None, "?", "zh"))  # unknown speaker: not scored
    save.write_fix({"rename": "說話者2", "name": "Boss"})
    save.write_fix({"merge": "說話者3", "into": "說話者1"})  # the user says 3 is really 1
    save.f.close()
    recs = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    assert [r.get("speaker") for r in recs[:6]] == ["說話者1", "說話者2", "說話者1", "說話者3", "說話者2", None]
    assert recs[0]["side"] == "them" and recs[-1]["fix"] == {"merge": "說話者3", "into": "說話者1"}
    r = subprocess.run([sys.executable, str(EVAL), "--transcript", str(path)], capture_output=True, text=True,
                       encoding="utf-8", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "lines with a speaker: 5 (corrected by the user: 1)" in r.stdout
    assert "accuracy x100: 80.00" in r.stdout  # 1 of 5 lines was split off into a wrong cluster
