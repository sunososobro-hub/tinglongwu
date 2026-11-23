"""#1747: GigaAM v3 (Russian ASR) on the CPU for the eval harness only; not a product engine.

`python -m tlwu_eval --smoke --pairs ru->zh --clips 20 --asr gigaam-v3 --gigaam-dir DIR --device cpu ...` scores it
with the same runner and WER as Whisper (the `ru->zh: N clips, ASR error rate` line of `tlwu_eval.langmatrix summary`).

Model: the sherpa-onnx export of GigaAM v3 (RNN-T with punctuation, int8 encoder; MIT), the same files and SHA-256
the mobile app pinned in mobile/assets/models_manifest.json before #563 (git show f1a69d1^:mobile/assets/...).
Nothing here touches the network: fetch and check the archive outside Python, e.g. in a workflow step,

    curl -fsSL -o giga.tar.bz2 <ARCHIVE_URL>
    echo "<ARCHIVE_SHA256>  giga.tar.bz2" | sha256sum -c - && tar xjf giga.tar.bz2   # -> DIR = ./<ARCHIVE_NAME>

and `pip install sherpa-onnx` (optional, eval only). Every model file is checked (size + SHA-256) before loading.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from tinglongwu.asr import Transcript

ARCHIVE_NAME = "sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16"
ARCHIVE_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ARCHIVE_NAME}.tar.bz2"
ARCHIVE_SHA256 = "f9620a0099019c6afcee26525ef9ed3297fa50dd5691c1902af0c948fc1a470b"
FILES = {  # name -> (size in bytes, sha256); copied from the mobile manifest of f1a69d1^
    "encoder.int8.onnx": (224570820, "369f35a71bf288d3b8e0391fabd8dba5f2314088d440bca474056b7b4b6e66bf"),
    "decoder.onnx": (4600132, "38fc7475443ea2a26f63211ca350f73ac50fff824ab7a3876ee2bd610c53bbc4"),
    "joiner.onnx": (2712896, "602ff7017a93311aad34df1437c8d7f49911353c13d6eae7a6ee7b041339465c"),
    "tokens.txt": (13354, "39abae20e692998290c574e606f11a9edef2902a1995463fcff63d1490cf22b7"),
}
LANG = "ru"
SAMPLE_RATE = 16000


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def check_dir(model_dir, files=None) -> dict[str, str]:
    """{name: path} of the pinned files in model_dir; FileNotFoundError / ValueError naming every missing or
    mismatching file (the runner turns both into exit 2)."""
    files = FILES if files is None else files
    d = Path(model_dir)
    missing = [n for n in files if not (d / n).is_file()]
    if missing:
        raise FileNotFoundError(f"GigaAM v3: {d} lacks {', '.join(missing)} (unpack {ARCHIVE_URL}, sha256 "
                                f"{ARCHIVE_SHA256}, and pass its folder as --gigaam-dir)")
    bad = [n for n, (size, sha) in files.items()
           if (d / n).stat().st_size != size or _sha256(d / n) != sha]
    if bad:
        raise ValueError(f"GigaAM v3: {', '.join(bad)} in {d} do not match the pinned size / SHA-256")
    return {n: str(d / n) for n in files}


def _sherpa_factory(paths: dict[str, str], threads: int):
    try:
        import sherpa_onnx
    except ImportError as e:
        raise RuntimeError("--asr gigaam-v3 needs the optional sherpa-onnx package (pip install sherpa-onnx)") from e
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=paths["encoder.int8.onnx"], decoder=paths["decoder.onnx"], joiner=paths["joiner.onnx"],
        tokens=paths["tokens.txt"], num_threads=threads, sample_rate=SAMPLE_RATE, decoding_method="greedy_search",
        model_type="nemo_transducer", provider="cpu")  # same config as the mobile app's asr_worker.dart (pre-#563)


class GigaAMRecognizer:
    """Runner ASR interface: .transcribe(float32 mono 16 kHz) -> Transcript (lang 'ru'), or None for empty audio.
    factory(paths, threads) -> a sherpa-onnx-like OfflineRecognizer (tests inject a stub)."""

    def __init__(self, model_dir, lang: str = LANG, threads: int | None = None, factory=None, files=None):
        if lang != LANG:
            raise ValueError(f"--asr gigaam-v3 only recognises Russian; got source language {lang!r} "
                             "(run it with --pairs ru->zh)")
        self._rec = (factory or _sherpa_factory)(check_dir(model_dir, files), threads or 4)

    def transcribe(self, audio):
        if audio is None or len(audio) == 0:
            return None
        stream = self._rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio)
        self._rec.decode_stream(stream)
        return Transcript((stream.result.text or "").strip(), LANG)
