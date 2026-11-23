"""#1753: scripts/bench/asr_backend_bench.py scores each ASR backend on the pinned clips and keeps going when one
backend cannot load (no Vulkan pack / GPU on this machine)."""
import importlib.util
import json
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("asr_backend_bench", ROOT / "scripts/bench/asr_backend_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

REFS = {"en": "the cat sat on the mat", "zh": "今天天氣很好"}


def _wav(path: Path, seconds: float, rate: int = 16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.zeros(int(rate * seconds), dtype="<i2")).tobytes())


@pytest.fixture
def data(tmp_path):
    rows = []
    for lang, ref in REFS.items():
        for i in range(2):
            name = f"{lang}_{i}.wav"
            _wav(tmp_path / name, 2.0)
            rows.append({"id": f"{lang}_{i}", "lang": lang, "wav": name, "ref_text": ref})
    (tmp_path / "manifest.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
    return tmp_path


def fake(hyps: dict):
    """load(size, lang) -> transcribe returning hyps[lang]; info() -> fixed sizes."""
    return (lambda size, lang: (lambda audio: hyps[lang])), (lambda size: {"lib_mb": 1.5, "model_mb": 2.0})


def test_scores_each_backend_and_language(data, tmp_path):
    out = []
    facs = {"fw-cpu": fake(REFS), "wcpp-cpu": fake({"en": "the cat sat on a mat", "zh": "今天天氣很"})}
    rc = bench.main(["--data", str(data), "--sizes", "small", "--backends", "fw-cpu,wcpp-cpu", "--langs", "en,zh",
                     "--models-dir", str(tmp_path), "--json", str(tmp_path / "r.json")], facs=facs, out=out.append)
    assert rc == 0
    res = json.loads((tmp_path / "r.json").read_text("utf-8"))
    fw, wc = res["sizes"]["small"]["fw-cpu"], res["sizes"]["small"]["wcpp-cpu"]
    assert fw["en"]["error"] == 0 and fw["zh"]["error"] == 0
    assert wc["en"] == {**wc["en"], "metric": "wer", "error": round(1 / 6, 4)}  # 1 substitution of 6 words
    assert wc["zh"]["metric"] == "cer" and wc["zh"]["error"] == round(1 / 6, 4)  # 1 deletion of 6 characters
    assert wc["en"]["audio_s"] == 4.0 and wc["en"]["rtf"] is not None
    table = out[-1]
    assert "| small | wcpp-cpu | zh | cer 0.167 |" in table
    assert "| wcpp-cpu | MIT (whisper.cpp, ggml) | 1.5 | small 2.0 |" in table


def test_missing_whisper_lib_is_unavailable_and_run_goes_on(data, tmp_path):
    out = []
    a = bench.parse_args(["--data", str(data), "--sizes", "small", "--backends", "fw-cpu,wcpp-vulkan",
                          "--models-dir", str(tmp_path), "--whisper-lib", str(tmp_path / "nope.so")])
    facs = {"fw-cpu": fake(REFS), "wcpp-vulkan": bench.wcpp_factory(a, use_gpu=True)}
    rc = bench.main(["--data", str(data), "--sizes", "small", "--backends", "fw-cpu,wcpp-vulkan",
                     "--models-dir", str(tmp_path), "--whisper-lib", str(tmp_path / "nope.so")], facs=facs,
                    out=out.append)
    assert rc == 0  # fw-cpu still ran
    assert "| small | wcpp-vulkan | - | unavailable: ImportError: whisper.cpp Vulkan library not found" in out[-1]
    assert "| small | fw-cpu | en | wer 0.000 |" in out[-1]


def test_all_backends_unavailable_exits_1(data, tmp_path):
    def broken(size, lang):
        raise FileNotFoundError("model missing")
    rc = bench.main(["--data", str(data), "--sizes", "small", "--backends", "fw-cpu", "--models-dir", str(tmp_path)],
                    facs={"fw-cpu": (broken, lambda s: {})}, out=lambda *_: None)
    assert rc == 1


def test_unknown_backend_rejected():
    with pytest.raises(SystemExit):
        bench.parse_args(["--data", ".", "--backends", "cuda"])


def test_reads_float32_wav_like_the_smoke_clips(tmp_path):
    """The make_ci_smoke.py clips are 32-bit float WAVs (format 3); the first CI run failed on them with wave."""
    sf = pytest.importorskip("soundfile")
    sf.write(str(tmp_path / "f.wav"), np.full(24000, 0.25, dtype=np.float32), 16000, subtype="FLOAT")
    audio, secs = bench.read_wav(tmp_path / "f.wav")
    assert secs == 1.5 and audio.dtype == np.float32 and abs(float(audio.max()) - 0.25) < 1e-4
