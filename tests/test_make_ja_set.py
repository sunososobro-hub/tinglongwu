"""#346 acceptance 1 / #429: scripts/bench/make_ja_set.py builds a >= 50-sentence news + >= 20-clip dialogue
ja -> zh-TW set that tlwu-eval runs and reports per style."""
import bz2
import importlib.util
import json
import shutil
import sys
import tarfile
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
    make_tatoeba(root)
    return root


DLG_JA = ["何してるの{}？", "ちょっと待って{}！", "それ本当{}？"]
DLG_CMN = ["你在做什么{}？", "等一下{}！", "那是真的吗{}？"]  # Simplified, as many Tatoeba cmn lines are


def make_tatoeba(root: Path, n=26, audio=True):
    """Tatoeba exports for n dialogue sentences (ids 100..) plus rejects: a CC BY-NC-ND recording, an unlicensed one,
    one without a cmn translation, one whose mp3 was not downloaded and a repeated sentence text."""
    d = root / "tatoeba_ja"
    (d / "audio").mkdir(parents=True)
    ja, cmn, links, swa = [], [], [], []

    def sent(sid, text, zh, lic, aid, user="yomi", mp3=True):
        ja.append(f"{sid}\tjpn\t{text}")
        if zh:
            cmn.append(f"{sid + 50000}\tcmn\t{zh}")
            cmn.append(f"{sid + 60000}\tcmn\t第二個翻譯")  # higher id: not used
            links.extend([f"{sid}\t{sid + 60000}", f"{sid}\t{sid + 50000}"])
        swa.append(f"{sid}\t{aid}\t{user}\t{lic}\thttps://example.invalid/{user}")
        if mp3 and audio:
            (d / "audio" / f"{aid}.mp3").write_bytes(b"ID3fake")

    sent(10, "撮らないで。", "别拍。", "CC BY-NC-ND 3.0", 9010)  # licence not allowed
    sent(11, "行こうよ。", "走吧。", "\\N", 9011)  # no licence
    sent(12, "翻訳なし。", None, "CC BY 4.0", 9012)  # no cmn translation
    sent(13, "音声なし。", "没有声音。", "CC BY 4.0", 9013, mp3=False)  # recording not fetched
    for i in range(n):
        sent(100 + i, DLG_JA[i % 3].format(i), DLG_CMN[i % 3].format(i), "CC BY-NC 4.0" if i % 2 else "CC BY 4.0",
             7000 + i, user="fal" if i % 2 else "yomi")
        if i == 2:
            sent(150, DLG_JA[2].format(2), "重複", "CC BY 4.0", 7500)  # same text as sentence 102: skipped
    swa.append("100\t7999\tlater\tCC BY 4.0\t")  # second recording of 100: the lower audio id is used
    for name, lines in (("jpn_sentences.tsv", ja), ("cmn_sentences.tsv", cmn), ("jpn-cmn_links.tsv", links),
                        ("sentences_with_audio.csv", swa)):
        (d / name).write_text("\n".join(lines) + "\n", "utf-8")
    return d


def test_tatoeba_candidates_keep_licensed_translated_recordings(tmp_path):
    make_tatoeba(tmp_path)
    cands = mjs.tatoeba_candidates(tmp_path)
    ids = [c["sentence"] for c in cands]
    assert "10" not in ids and "11" not in ids and "12" not in ids  # ND licence, no licence, no translation
    assert ids[0] == "13" and ids[1:4] == ["100", "101", "102"]  # ordered by sentence id
    c100 = cands[1]
    assert c100["audio"] == "7000" and c100["cmn"] == "你在做什么0？" and c100["license"] == "CC BY 4.0"


def test_select_dialogue_converts_refs_to_traditional_and_skips_missing_audio(tmp_path):
    make_tatoeba(tmp_path)
    rows = mjs.select_dialogue(tmp_path, 100)
    assert len(rows) == 26  # 13 has no mp3, 150 repeats 102
    assert rows[0]["ja"] == "何してるの0？" and rows[0]["ref_zh"] == "你在做什麼0？"
    assert rows[2]["ref_zh"] == "那是真的嗎2？" and rows[1]["license"] == "CC BY-NC 4.0"
    assert mjs.select_dialogue(tmp_path / "nothing", 30) == []


def test_select_matches_fleurs_to_flores_zh_hant(tmp_path):
    rows, dropped = mjs.select(make_sources(tmp_path), 60)
    assert len(rows) == 60 and dropped == 2
    wav, transcript, ja, zh = rows[0]
    assert wav.name == "1000.wav" and ja == "東京で会議0がありました。" and zh == "東京舉行了第0場會議。"
    assert [r[3] for r in rows] == [f"東京舉行了第{i}場會議。" for i in range(60)]  # repeat skipped, order kept


def test_cli_writes_manifest_tlwu_eval_runs_it(tmp_path, capsys):
    src, out = make_sources(tmp_path / "src"), tmp_path / "ja"
    assert mjs.main(["--data", str(src), "--out", str(out)]) == 0
    assert "ja set: 60 news sentences + 26 dialogue clips" in capsys.readouterr().out
    rows = load_manifest(out)
    assert len(rows) == 86 and {r["lang"] for r in rows} == {"ja"} and all(Path(r["wav"]).is_file() for r in rows)
    news = [r for r in rows if r["style"] == "news"]
    dlg = [r for r in rows if r["style"] == "dialogue"]
    assert len(news) == 60 and len(dlg) == 26
    assert rows[5]["ref_zh"] == "東京舉行了第5場會議。" and rows[5]["source"] == "fleurs:ja_jp/test/1005.wav"
    assert dlg[0]["id"] == "ja_dialogue_001" and dlg[0]["wav"].endswith("ja_dialogue_001.mp3")
    assert dlg[0]["ref_text"] == "何してるの0？" and dlg[0]["ref_zh"] == "你在做什麼0？"
    assert dlg[0]["source"] == "tatoeba:sentence/100/audio/7000"
    lic = (out / "LICENSE.txt").read_text("utf-8")
    assert "CC-BY 4.0" in lic and "CC-BY-SA 4.0" in lic and "CC-BY 2.0 FR" in lic
    assert "ja_dialogue_002.mp3: sentence https://tatoeba.org/sentences/show/101, audio 7001 by fal, CC BY-NC 4.0" in lic
    clock = Clock()
    eng = fake_engines(clock)
    eng.read_audio = lambda path: (np.zeros(32000, np.float32), 2.0)  # the fixture mp3s are placeholders
    report = tmp_path / "r.json"
    assert eval_main(["--smoke", "--data", str(out), "--out", str(report), "--per-lang", "60", "--no-noise"],
                     engines=eng, totals=(16000, None), clock=clock) == 0
    rep = json.loads(report.read_text("utf-8"))
    by_pair = {lg["pair"]: lg for lg in rep["languages"]}
    assert len(by_pair["ja->zh"]["clips"]) == 60 and by_pair["ja->zh"]["chrf"] is not None
    assert [c["id"] for c in by_pair["ja->zh[dialogue]"]["clips"]] == [f"ja_dialogue_{i:03d}" for i in range(1, 27)]
    assert by_pair["ja->zh[dialogue]"]["chrf"] is not None


def test_too_few_sentences_fails(tmp_path, capsys):
    src = make_sources(tmp_path / "src", n_flores=20)
    assert mjs.main(["--data", str(src), "--out", str(tmp_path / "ja")]) == 1
    assert "need >= 50" in capsys.readouterr().err


def test_missing_dialogue_fails(tmp_path, capsys):
    src = make_sources(tmp_path / "src")
    shutil.rmtree(src / "tatoeba_ja")
    assert mjs.main(["--data", str(src), "--out", str(tmp_path / "ja")]) == 1
    assert "only 0 dialogue clips, need >= 20" in capsys.readouterr().err
    assert mjs.main(["--data", str(src), "--out", str(tmp_path / "ja2"), "--min-dialogue", "0"]) == 0


def test_fetch_tatoeba_downloads_exports_and_only_usable_recordings(tmp_path):
    spec2 = importlib.util.spec_from_file_location("fetch_data", ROOT / "scripts" / "bench" / "fetch_data.py")
    fd = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(fd)
    src = make_tatoeba(tmp_path / "src", n=5, audio=False)
    tar = tmp_path / "swa.tar.bz2"
    with tarfile.open(tar, "w:bz2") as t:
        t.add(src / "sentences_with_audio.csv", arcname="sentences_with_audio.csv")
    served = {fd.TATOEBA_EXPORTS + "sentences_with_audio.tar.bz2": tar.read_bytes()}
    for name, rel in fd.TATOEBA_FILES.items():
        served[fd.TATOEBA_EXPORTS + rel] = bz2.compress((src / name).read_bytes())
    got = []

    def get(url, dest):
        got.append(url)
        dest.write_bytes(served.get(url, b"ID3mp3"))
        return dest

    d = fd.fetch_tatoeba_ja(tmp_path / "data", 3, get=get)
    audio = [u.rsplit("/", 1)[1] for u in got if u.startswith("https://tatoeba.org/audio/")]
    assert audio == ["9013", "7000", "7001"]  # allowed licence + translation only, first 3 by sentence id
    assert sorted(p.name for p in (d / "audio").iterdir()) == ["7000.mp3", "7001.mp3", "9013.mp3"]
    assert (d / "jpn-cmn_links.tsv").read_text("utf-8") == (src / "jpn-cmn_links.tsv").read_text("utf-8")
    fd.fetch_tatoeba_ja(tmp_path / "data", 3, get=get)
    assert len(got) == 7  # second run: everything already there, nothing downloaded again
