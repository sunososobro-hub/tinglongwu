"""#1397 r2: the standard ru set (eval-langs FLEURS clips) with vs without the default hotwords decides shipping."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("acronym_eval_std", REPO / "scripts" / "bench" / "acronym_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeAsr:
    """Without hotwords: the reference text. With them: `noisy` inserts a list acronym (the harm the gate catches)."""

    def __init__(self, refs: dict, noisy: bool):
        self.refs, self.noisy, self.glossary, self._last = refs, noisy, None, ""

    def transcribe(self, x):
        text = self.refs[int(x[0])]
        if self.glossary is not None and self.noisy:
            text += " API"
        return type("T", (), {"text": text})()


def _manifest(tmp: Path) -> dict:
    rows = [{"id": "ru_001", "lang": "ru", "wav": "ru_001.wav", "ref_text": "Сегодня в городе идёт сильный дождь."},
            {"id": "ru_002", "lang": "ru", "wav": "ru_002.wav", "ref_text": "Мы встретимся завтра у вокзала."},
            {"id": "en_001", "lang": "en", "wav": "en_001.wav", "ref_text": "Not a Russian clip."}]
    (tmp / "manifest.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    return {i: r["ref_text"] for i, r in enumerate(rows)}


def _run_std(tmp: Path, monkeypatch, noisy: bool) -> Path:
    m = _mod()
    refs = _manifest(tmp)
    by_wav = {f"{r}": i for i, r in enumerate(["ru_001.wav", "ru_002.wav", "en_001.wav"])}
    import india_eval  # noqa: F401  (scripts/bench on sys.path after cmd_std's insert)
    monkeypatch.setattr(india_eval, "load_audio", lambda p: np.full(1600, by_wav[Path(p).name], np.float32))
    import tinglongwu.asr as asr_mod
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", lambda *a, **k: FakeAsr(refs, noisy))
    out = tmp / "out"
    assert m.main(["std", "--data", str(tmp), "--out", str(out), "--models-dir", str(tmp)]) == 0
    return out


def _acronym_rows(out: Path, helps: bool):
    """A tiny acronym-set result: one acronym clip that the list fixes (or not), one clean distractor."""
    rows = [{"id": "a1", "term": "API", "ref_text": "Документация к API лежит тут.",
             "asr_no": "Документация к эпиай лежит тут.",
             "asr_hw": "Документация к API лежит тут." if helps else "Документация к эпиай лежит тут.",
             "asr_no_s": 1.0, "asr_hw_s": 1.0},
            {"id": "d1", "term": "", "ref_text": "Пиар отдел занят.", "asr_no": "Пиар отдел занят.",
             "asr_hw": "Пиар отдел занят.", "asr_no_s": 1.0, "asr_hw_s": 1.0}]
    (out / "acronyms.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


sys.path.insert(0, str(REPO / "scripts" / "bench"))
sys.path.insert(0, str(REPO / "scripts"))


def test_std_decodes_only_ru_clips_with_and_without(tmp_path, monkeypatch):
    out = _run_std(tmp_path, monkeypatch, noisy=False)
    rows = [json.loads(x) for x in (out / "std_ru.jsonl").read_text("utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["ru_001", "ru_002"]
    assert all(r["asr_no"] == r["asr_hw"] == r["ref_text"] for r in rows)


def test_report_ships_when_acronyms_help_and_std_wer_holds(tmp_path, monkeypatch, capsys):
    out = _run_std(tmp_path, monkeypatch, noisy=False)
    _acronym_rows(out, helps=True)
    assert _mod().main(["report", str(out)]) == 0
    s = json.loads((out / "summary.json").read_text("utf-8"))
    assert s["std"]["no"]["wer"] == s["std"]["hw"]["wer"] == 0.0 and s["std"]["within"] is True
    assert s["ship"] is True
    assert "Ship the default ru list: yes" in capsys.readouterr().out


def test_report_refuses_when_std_wer_gets_worse(tmp_path, monkeypatch, capsys):
    out = _run_std(tmp_path, monkeypatch, noisy=True)  # the list inserts "API" into every standard clip
    _acronym_rows(out, helps=True)
    _mod().main(["report", str(out)])
    s = json.loads((out / "summary.json").read_text("utf-8"))
    assert s["std"]["hw"]["wer"] > s["std"]["no"]["wer"] + 0.005 and s["std"]["within"] is False
    assert s["ship"] is False and s["helps"] is True
    assert "Ship the default ru list: no" in capsys.readouterr().out


def test_report_without_std_run_says_unknown(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    _acronym_rows(out, helps=True)
    _mod().main(["report", str(out)])
    assert json.loads((out / "summary.json").read_text("utf-8"))["ship"] is None
    assert "unknown (standard set not run)" in capsys.readouterr().out
