"""#346 acceptance 1: scripts/bench/make_ja_set.py builds a >= 50-sentence ja -> zh-TW set that tlwu-eval runs."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
spec = importlib.util.spec_from_file_location("make_ja_set", ROOT / "scripts" / "bench" / "make_ja_set.py")
mjs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mjs)

from test_eval_smoke import Clock, fake_engines  # noqa: E402
from tlwu_eval.__main__ import main as eval_main  # noqa: E402
from tlwu_eval.runner import load_manifest  # noqa: E402


def make_sources(root: Path, n_flores=70):
    """FLORES dev/devtest jpn_Jpan + zho_Hant (with a blank line) and a FLEURS ja_jp test.tsv whose transcripts carry
    FLEURS-style spaces/full-width chars, a repeated sentence, an unknown sentence and a row whose wav is missing."""
    fl = root / "flores200_dataset"
    half = n_flores // 2
    for split, rng in (("dev", range(0, half)), ("devtest", range(half, n_flores))):
        (fl / split).mkdir(parents=True)
        (fl / split / f"jpn_Jpan.{split}").write_text("".join(f"東京で会議{i}がありました。\n" for i in rng) + "\n", "utf-8")
        (fl / split / f"zho_Hant.{split}").write_text("".join(f"東京舉行了第{i}場會議。\n" for i in rng) + "\n", "utf-8")
    fe = root / "fleurs_ja"
    (fe / "audio" / "test").mkdir(parents=True)
    lines = []

    def utt(fid, text, wav=True):
        name = f"{fid}.wav"
        if wav:
            sf.write(fe / "audio" / "test" / name, np.zeros(32000, np.float32), 16000)
        lines.append(f"{fid}\t{name}\t{text}\t{text}\tx\t32000\tFEMALE")

    utt(9000, "これはFLORESにない文です")  # unmatched
    utt(9001, "東京で会議0がありました。", wav=False)  # audio missing -> dropped, the sentence can still match later
    for i in range(n_flores):
        utt(1000 + i, f"東京 で 会議{chr(0xFF10 + i % 10) if i < 10 else i} が ありました")  # spaces, full-width digit
        if i == 3:
            utt(5000, "東京で会議3がありました。")  # repeat of a kept sentence
    (fe / "test.tsv").write_text("\n".join(lines) + "\n", "utf-8")
    return root


def test_select_matches_fleurs_to_flores_zh_hant(tmp_path):
    rows, dropped = mjs.select(make_sources(tmp_path), 60)
    assert len(rows) == 60 and dropped == 2
    wav, transcript, ja, zh = rows[0]
    assert wav.name == "1000.wav" and ja == "東京で会議0がありました。" and zh == "東京舉行了第0場會議。"
    assert [r[3] for r in rows] == [f"東京舉行了第{i}場會議。" for i in range(60)]  # repeat skipped, order kept


def test_cli_writes_manifest_tlwu_eval_runs_it(tmp_path, capsys):
    src, out = make_sources(tmp_path / "src"), tmp_path / "ja"
    assert mjs.main(["--data", str(src), "--out", str(out)]) == 0
    assert "ja set: 60 sentences" in capsys.readouterr().out
    rows = load_manifest(out)
    assert len(rows) == 60 and {r["lang"] for r in rows} == {"ja"} and all(Path(r["wav"]).is_file() for r in rows)
    assert rows[5]["ref_zh"] == "東京舉行了第5場會議。" and rows[5]["source"] == "fleurs:ja_jp/test/1005.wav"
    lic = (out / "LICENSE.txt").read_text("utf-8")
    assert "CC-BY 4.0" in lic and "CC-BY-SA 4.0" in lic
    clock = Clock()
    report = tmp_path / "r.json"
    assert eval_main(["--smoke", "--data", str(out), "--out", str(report), "--per-lang", "60"],
                     engines=fake_engines(clock), totals=(16000, None), clock=clock) == 0
    rep = json.loads(report.read_text("utf-8"))
    ja = [lg for lg in rep["languages"] if lg["pair"] == "ja->zh"]
    assert len(ja) == 1 and len(ja[0]["clips"]) == 60 and ja[0]["chrf"] is not None


def test_too_few_sentences_fails(tmp_path, capsys):
    src = make_sources(tmp_path / "src", n_flores=20)
    assert mjs.main(["--data", str(src), "--out", str(tmp_path / "ja")]) == 1
    assert "need >= 50" in capsys.readouterr().err
