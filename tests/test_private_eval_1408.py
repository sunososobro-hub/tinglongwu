"""#1408: private recordings eval prints numbers only, keeps a local history and flags a > 2 pt regression."""
from __future__ import annotations

import importlib.util
import json
import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("private_eval_1408", ROOT / "scripts" / "private_eval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pe = _load()

SECRET_NAME = "meeting_with_ivanov"
REF = "сегодня мы обсуждаем новый релиз и общий план на неделю"


def _tone(path: Path, seconds: float = 2.0, sr: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / sr)))
                               for i in range(int(seconds * sr))))


def _folder(tmp_path: Path, n: int = 1) -> Path:
    d = tmp_path / "private"
    d.mkdir()
    for k in range(n):
        name = f"{SECRET_NAME}_{k}"
        _tone(d / f"{name}.wav", 2.0 + k)
        (d / f"{name}.ref.txt").write_text(REF, "utf-8")
    return d


def _fake(hyp: str, seen=None):
    def t(wav, lang, models_dir=None, model=None):
        if seen is not None:
            seen.append((Path(wav).name, lang, model))
        return hyp
    return t


def _no_private_text(out: str, d: Path) -> None:
    assert SECRET_NAME not in out
    assert str(d) not in out
    for word in REF.split():
        if len(word) > 3:
            assert word not in out


def test_unset_or_empty_folder_skips(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(pe.ENV, raising=False)
    assert pe.main(["run", "--summary", str(tmp_path / "s.md")]) == 0
    assert "SKIP private eval" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(pe.ENV, str(empty))
    assert pe.main(["run"]) == 0
    assert "SKIP private eval" in capsys.readouterr().out


def test_numbers_only_output_and_summary(tmp_path, capsys):
    d = _folder(tmp_path, 2)
    summary = tmp_path / "summary.md"
    seen = []
    hyp = "сегодня мы обсуждаем новый релиз и общий план на месяц"  # 1 error in 10 words
    assert pe.run(d, summary=summary, transcriber=_fake(hyp, seen)) == 0
    cap = capsys.readouterr()
    out = cap.out + cap.err
    assert "file 1:" in out and "file 2:" in out
    assert "WER  10.00%" in out
    assert "2.0 s" in out and "3.0 s" in out
    assert "new" in out and "WARN" not in out
    _no_private_text(out, d)
    _no_private_text(summary.read_text("utf-8"), d)
    assert [s[1] for s in seen] == ["ru", "ru"]  # default language
    assert all(s[2] is None for s in seen)  # no --model: the shipped default choice


def test_delta_and_regression_warning(tmp_path, capsys):
    d = _folder(tmp_path)
    pe.run(d, transcriber=_fake(REF))  # 0 %
    capsys.readouterr()
    pe.run(d, transcriber=_fake("сегодня мы обсуждаем новый релиз и общий план на месяц"))  # 10 %: +10 pt
    out = capsys.readouterr().out
    assert "+10.00 pt" in out
    assert "WARN private eval regressed" in out
    _no_private_text(out, d)
    pe.run(d, transcriber=_fake("сегодня мы обсуждаем новый релиз и общий план на месяц"))  # same: +0 pt
    out = capsys.readouterr().out
    assert "+0.00 pt" in out and "WARN" not in out


def test_small_change_is_not_a_regression(tmp_path, capsys):
    d = _folder(tmp_path)
    long_ref = " ".join(["слово"] * 100)
    (d / f"{SECRET_NAME}_0.ref.txt").write_text(long_ref, "utf-8")
    pe.run(d, transcriber=_fake(long_ref))
    pe.run(d, transcriber=_fake(" ".join(["слово"] * 98)))  # +2.0 pt exactly: not > 2.0
    out = capsys.readouterr().out
    assert "+2.00 pt" in out and "WARN" not in out


def test_partial_reference_and_lang_file(tmp_path, capsys):
    d = _folder(tmp_path)
    (d / f"{SECRET_NAME}_0.lang").write_text("en\n", "utf-8")
    (d / f"{SECRET_NAME}_0.ref.txt").write_text("we ship the new release", "utf-8")
    hyp = "hello everyone today we ship the new release and then lunch"
    seen = []
    pe.run(d, transcriber=_fake(hyp, seen))
    assert seen[0][1] == "en"
    assert "WER 120.00%" in capsys.readouterr().out  # whole-file scoring: 6 insertions / 5 words
    (d / f"{SECRET_NAME}_0.partial").write_text("", "utf-8")
    pe.run(d, transcriber=_fake(hyp))
    assert "WER   0.00%" in capsys.readouterr().out  # aligned to the covered window


def test_transcriber_error_hides_the_message(tmp_path, capsys):
    d = _folder(tmp_path)

    def boom(wav, lang, models_dir=None, model=None):
        raise RuntimeError(f"cannot read {wav}")

    assert pe.run(d, transcriber=boom) == 0
    out = capsys.readouterr().out
    assert "error (RuntimeError)" in out
    _no_private_text(out, d)


def test_transcribe_runs_the_shipped_cli_and_discards_its_output(tmp_path, monkeypatch):
    wav = tmp_path / "x.wav"
    _tone(wav)
    calls = {}

    class R:
        returncode = 0

    def fake_run(cmd, stdout=None, stderr=None, **kw):
        calls["cmd"], calls["io"] = cmd, (stdout, stderr)
        out = Path(cmd[cmd.index("--save") + 1])
        out.write_text(json.dumps({"src": "привет"}) + "\n" + json.dumps({"src": "мир", "dst": ""}) + "\n", "utf-8")
        return R()

    monkeypatch.setattr(pe.subprocess, "run", fake_run)
    assert pe.transcribe(wav, "ru", "M") == "привет мир"
    cmd = calls["cmd"]
    assert cmd[1:3] == ["-m", "tinglongwu"] and "--transcript-only" in cmd and "--model" not in cmd
    assert cmd[cmd.index("--lang") + 1] == "ru" and cmd[cmd.index("--models-dir") + 1] == "M"
    assert calls["io"] == (pe.subprocess.DEVNULL, pe.subprocess.DEVNULL)


def test_bootstrap_writes_a_draft_with_the_largest_model(tmp_path, capsys):
    d = _folder(tmp_path)
    wav = d / f"{SECRET_NAME}_0.wav"
    (d / f"{SECRET_NAME}_0.ref.txt").unlink()
    mdir = tmp_path / "models"
    for s in ("small", "medium", "tiny"):
        (mdir / f"whisper-{s}").mkdir(parents=True)
    seen = []
    assert pe.bootstrap(wav, str(mdir), transcriber=_fake("черновик текста", seen)) == 0
    assert (d / f"{SECRET_NAME}_0.ref.txt").read_text("utf-8") == "черновик текста\n"
    assert seen[0][2] == "medium"
    assert pe.bootstrap(wav, str(mdir), transcriber=_fake("x")) == 1  # never overwrites a proofread reference
    assert (d / f"{SECRET_NAME}_0.ref.txt").read_text("utf-8") == "черновик текста\n"
