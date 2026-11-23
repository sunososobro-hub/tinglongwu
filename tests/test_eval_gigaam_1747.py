"""#1747: GigaAM v3 CPU ASR adapter for the eval harness (stub sherpa-onnx model; no download, no network)."""
import hashlib
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from tlwu_eval import gigaam, langmatrix, runner
from tlwu_eval.__main__ import gigaam_loader

from test_eval_smoke import REFS, Clock, fake_engines, make_data


def _pinned_dir(tmp_path, monkeypatch):
    """A model folder with tiny stand-in files, and FILES pinned to exactly those bytes."""
    d = tmp_path / "giga"
    d.mkdir()
    pins = {}
    for name in gigaam.FILES:
        data = f"stub {name}".encode()
        (d / name).write_bytes(data)
        pins[name] = (len(data), hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(gigaam, "FILES", pins)
    return d


class StubRec:
    """sherpa-onnx OfflineRecognizer stand-in: records what it was built with and what each stream received."""

    def __init__(self, paths, threads, text):
        self.paths, self.threads, self.text, self.fed = paths, threads, text, []

    def create_stream(self):
        rec = self

        class Stream:
            result = SimpleNamespace(text="")

            def accept_waveform(self, sr, audio):
                rec.fed.append((sr, len(audio), audio.dtype))

        return Stream()

    def decode_stream(self, stream):
        stream.result = SimpleNamespace(text=f"  {self.text}  ")


def test_transcribe_feeds_16k_audio_and_returns_russian_transcript(tmp_path, monkeypatch):
    d = _pinned_dir(tmp_path, monkeypatch)
    made = []

    def factory(paths, threads):
        made.append(StubRec(paths, threads, "кошка сидела на коврике"))
        return made[-1]

    asr = gigaam.GigaAMRecognizer(d, "ru", threads=2, factory=factory)
    tr = asr.transcribe(np.zeros(32000, np.float32))
    assert (tr.text, tr.lang) == ("кошка сидела на коврике", "ru")
    rec = made[0]
    assert rec.fed == [(16000, 32000, np.float32)] and rec.threads == 2
    assert set(rec.paths) == set(gigaam.FILES) and rec.paths["encoder.int8.onnx"] == str(d / "encoder.int8.onnx")
    assert asr.transcribe(np.zeros(0, np.float32)) is None


def test_missing_or_tampered_model_files_and_wrong_language_are_refused(tmp_path, monkeypatch):
    d = _pinned_dir(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="only recognises Russian"):
        gigaam.GigaAMRecognizer(d, "en", factory=lambda p, t: None)
    (d / "joiner.onnx").write_bytes(b"stub joiner.onnX")  # same size, different bytes
    with pytest.raises(ValueError, match="joiner.onnx .* do not match"):
        gigaam.check_dir(d)
    (d / "tokens.txt").unlink()
    with pytest.raises(FileNotFoundError, match="lacks tokens.txt"):
        gigaam.check_dir(d)


def test_real_pins_match_the_mobile_manifest_shape():
    assert set(gigaam.FILES) == {"encoder.int8.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"}
    assert all(size > 0 and len(sha) == 64 for size, sha in gigaam.FILES.values()) and len(gigaam.ARCHIVE_SHA256) == 64


def test_loader_scores_ru_with_the_same_wer_as_whisper(tmp_path, monkeypatch):
    d = _pinned_dir(tmp_path, monkeypatch)
    words = REFS["ru"][0].split()
    hyp = " ".join(words[:-1] + ["ковре"])  # one of four words wrong -> 25% WER
    a = SimpleNamespace(asr="gigaam-v3", gigaam_dir=str(d), asr_threads=None)
    load = gigaam_loader(a, "cpu", factory=lambda paths, threads: StubRec(paths, threads, hyp))
    clock = Clock()
    eng = fake_engines(clock)
    eng.load_asr = load
    (tmp_path / "data").mkdir()
    rows = runner.load_manifest(make_data(tmp_path / "data", langs=("ru",), n=20))
    rep = runner.run_smoke(rows, eng, clock=clock, ram=lambda: 1.0, vram=lambda: None, totals=(16000, None),
                           pairs=(("ru", "zh"),), ja_extra=[], per_lang=20)
    ok, line = langmatrix.summary_line(rep, "ru->zh")
    assert ok and line.startswith("ru->zh: 20 clips, ASR error rate 25.00%")


def test_loader_setup_errors(tmp_path, monkeypatch):
    assert gigaam_loader(SimpleNamespace(asr="whisper"), "cpu") is None
    with pytest.raises(ValueError, match="CPU only"):
        gigaam_loader(SimpleNamespace(asr="gigaam-v3", gigaam_dir="x"), "cuda")
    with pytest.raises(ValueError, match="needs --gigaam-dir"):
        gigaam_loader(SimpleNamespace(asr="gigaam-v3", gigaam_dir=None), "cpu")


def test_cli_without_model_folder_is_a_clear_setup_error(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    make_data(data, langs=("ru",), n=2)
    r = subprocess.run([sys.executable, "-m", "tlwu_eval", "--smoke", "--data", str(data), "--pairs", "ru->zh",
                        "--asr", "gigaam-v3", "--gigaam-dir", str(tmp_path / "none"), "--device", "cpu", "--no-noise",
                        "--out", str(tmp_path / "r.json")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "lacks encoder.int8.onnx" in r.stderr and "Traceback" not in r.stderr
