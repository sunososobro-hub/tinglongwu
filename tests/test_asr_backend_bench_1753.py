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


def test_vulkan_request_that_fell_back_to_cpu_is_rejected():
    """CodeRabbit (PR #1755): use_gpu=True only asks for a GPU; whisper.cpp logs `no GPU found` and runs on its CPU
    backend (a --cpu build, no driver). That must not be reported as a Vulkan number."""
    with pytest.raises(RuntimeError, match="did not use Vulkan"):
        bench.check_vulkan("whisper_backend_init_gpu: no GPU found\n")
    with pytest.raises(RuntimeError, match="did not use Vulkan"):
        bench.check_vulkan("whisper_backend_init_gpu: using CUDA0 backend\n")
    with pytest.raises(RuntimeError, match="did not use Vulkan"):  # "using" is logged before init (pinned whisper.cpp)
        bench.check_vulkan("whisper_backend_init_gpu: using Vulkan0 backend\n"
                           "whisper_backend_init_gpu: failed to initialize Vulkan0 backend\n")
    bench.check_vulkan("ggml_vulkan: Found 1 Vulkan devices:\nwhisper_backend_init_gpu: using Vulkan0 backend\n")


def test_capture_stderr_sees_native_fd2_writes():
    import os

    result, log = bench.capture_stderr(lambda: os.write(2, b"whisper_backend_init_gpu: no GPU found\n") and 7)
    assert result == 7 and "no GPU found" in log


def test_unavailable_error_stays_in_one_table_row(data, tmp_path):
    def broken(size, lang):
        raise FileNotFoundError("Whisper model 'small' missing:\n  run tlwu-setup | --whisper small")
    out = []
    bench.main(["--data", str(data), "--sizes", "small", "--backends", "fw-cpu,wcpp-cpu", "--models-dir", str(tmp_path)],
               facs={"fw-cpu": (broken, lambda s: {}), "wcpp-cpu": fake(REFS)}, out=out.append)
    row = [ln for ln in out[-1].splitlines() if "unavailable" in ln]
    assert row == ["| small | fw-cpu | - | unavailable: FileNotFoundError: Whisper model 'small' missing: run "
                   "tlwu-setup \\| --whisper small | - |"]
