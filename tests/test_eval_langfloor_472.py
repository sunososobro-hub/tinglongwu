"""#472 (#335 part 3): smoke eval for it/pt/vi/th/id -> zh prints COMET x100 per language against the 75.00 floor."""
import json
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from tlwu_eval import langfloor, runner, scoring
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock
from test_eval_smoke import make_data as smoke_data

REFS = {"it": "il gatto è sul tappeto", "pt": "o gato está no tapete", "vi": "con mèo ngồi trên thảm",
        "th": "แมวนั่งบนเสื่อ", "id": "kucing duduk di atas tikar"}
ZH = "貓坐在墊子上"


def make_data(tmp_path, n=5):
    rows = []
    for lang in runner.HYMT_NEW_LANGS:
        for i in range(n):
            name = f"{lang}_{i}.wav"
            sf.write(tmp_path / name, np.zeros(32000, np.float32), 16000)
            rows.append({"id": name[:-4], "lang": lang, "wav": name, "ref_text": REFS[lang], "ref_zh": ZH})
    (tmp_path / "manifest.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
    return tmp_path


def engines(clock):
    class Asr:
        def __init__(self, lang):
            self.lang = lang

        def transcribe(self, audio):
            clock.t += 0.2
            return SimpleNamespace(text=REFS[self.lang], lang=self.lang)

    class Mt:
        def translate(self, text, src, tgt):
            clock.t += 0.1
            return SimpleNamespace(text=ZH)

    return runner.Engines(load_asr=Asr, load_mt=Mt)


class Comet:
    """Scores by the source language of the batch: vi 74.996 (rounds to 75.00 but is below), th below, id on it."""
    SCORES = {"it": 0.8312, "pt": 0.8001, "vi": 0.74996, "th": 0.7499, "id": 0.75}

    def predict(self, data, batch_size, gpus, progress_bar):
        lang = next(k for k, v in REFS.items() if v == data[0]["src"])
        assert len(data) == 5 and all(d["ref"] == ZH for d in data)
        return SimpleNamespace(system_score=self.SCORES[lang])


def test_langs_run_new_pairs_and_print_comet_floor_table(tmp_path, capsys):
    data, out, clock = make_data(tmp_path), tmp_path / "r.json", Clock()
    (tmp_path / "nhk").mkdir()
    nhk = smoke_data(tmp_path / "nhk", langs=("ja",))  # an NHK ja folder: must be ignored by --langs
    code = main(["--smoke", "--data", str(data), "--out", str(out), "--no-noise", "--langs", "IT,pt,vi,th,id,it",
                 "--nhk-dir", str(nhk), "--comet"], engines=engines(clock), totals=(16000, None), clock=clock, comet_model=Comet())
    printed = capsys.readouterr().out
    rep = json.loads(out.read_text("utf-8"))
    assert code == 0
    assert [lg["pair"] for lg in rep["languages"]] == ["it->zh", "pt->zh", "vi->zh", "th->zh", "id->zh"]
    assert all(len(lg["clips"]) == 5 for lg in rep["languages"])
    assert [(r["pair"], r["comet"], r["verdict"]) for r in rep["comet_floor"]] == [
        ("it->zh", 83.12, "supported"), ("pt->zh", 80.01, "supported"), ("vi->zh", 75.0, "stays Experimental"),
        ("th->zh", 74.99, "stays Experimental"), ("id->zh", 75.0, "supported")]
    assert "| pair | COMET x100 | floor 75.00 |" in printed
    assert "| th->zh | 74.99 | stays Experimental |" in printed and "| id->zh | 75.00 | supported |" in printed


def test_missing_language_stays_experimental():
    rows = langfloor.verdicts({"it->zh": 90.0}, [("it", "zh"), ("th", "zh")])
    assert [r["verdict"] for r in rows] == ["supported", "stays Experimental"]
    assert "| th->zh | n/a (no clips with ref_zh) | stays Experimental |" in langfloor.table(rows)


def test_thai_asr_is_scored_by_characters():
    # Thai has no spaces: one wrong letter must not count as a 100% word error
    assert scoring.error_rate([("แมวนั่งบนเสื่อ", "แมวนั่งบนเสือ")], "th") < 0.2
