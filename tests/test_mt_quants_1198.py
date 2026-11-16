"""#1198 next cut: the CPU bench compares lower-bit Hy-MT2 GGUFs; quant names must map to the right repo files."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _bench():
    spec = importlib.util.spec_from_file_location("mt_cpu_threads", ROOT / "scripts/bench/mt_cpu_threads.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


REPO = ["README.md", "Hy-MT2-1.8B-Q4_K_M.gguf", "Hy-MT2-1.8B-Q3_K_M.gguf", "Hy-MT2-1.8B-Q3_K_S.gguf",
        "Hy-MT2-1.8B-Q8_0.gguf", "Hy-MT2-1.8B-IQ4_XS.gguf", "imatrix/Hy-MT2-1.8B-Q2_K.imatrix.gguf",
        "split/Hy-MT2-1.8B-Q2_K-00001-of-00002.gguf"]


def test_pick_quants_maps_exact_quant_not_prefix():
    got = _bench().pick_quants(REPO, ["Q3_K_M", "Q3_K_S", "q4_k_m", "IQ4_XS"])
    assert got == {"Q3_K_M": "Hy-MT2-1.8B-Q3_K_M.gguf", "Q3_K_S": "Hy-MT2-1.8B-Q3_K_S.gguf",
                   "q4_k_m": "Hy-MT2-1.8B-Q4_K_M.gguf", "IQ4_XS": "Hy-MT2-1.8B-IQ4_XS.gguf"}


def test_pick_quants_missing_split_and_imatrix_are_none():
    got = _bench().pick_quants(REPO, ["Q2_K", "Q3_K", "IQ3_M"])
    assert got == {"Q2_K": None, "Q3_K": None, "IQ3_M": None}


def test_local_quants_finds_downloaded_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "Hy-MT2-1.8B-Q3_K_M.gguf").write_bytes(b"x")
    got = _bench().local_quants(tmp_path, ["Q3_K_M", "Q2_K"])
    assert got == {"Q3_K_M": str(tmp_path / "sub" / "Hy-MT2-1.8B-Q3_K_M.gguf"), "Q2_K": None}
    assert _bench().local_quants(None, ["Q3_K_M"]) == {"Q3_K_M": None}


def test_fetch_quants_needs_quant_dir():
    with pytest.raises(SystemExit):
        _bench().main(["--fetch-quants", "--quants", "Q3_K_M"])


def test_local_quants_finds_uppercase_extension(tmp_path):
    (tmp_path / "Hy-MT2-1.8B-Q3_K_S.GGUF").write_bytes(b"x")
    assert _bench().local_quants(tmp_path, ["Q3_K_S"]) == {"Q3_K_S": str(tmp_path / "Hy-MT2-1.8B-Q3_K_S.GGUF")}


def test_pick_source_prefers_full_precision():
    assert _bench().pick_source(REPO) == ("Q8_0", "Hy-MT2-1.8B-Q8_0.gguf")
    assert _bench().pick_source(REPO + ["Hy-MT2-1.8B-F16.gguf"]) == ("F16", "Hy-MT2-1.8B-F16.gguf")
    assert _bench().pick_source(["Hy-MT2-1.8B-Q4_K_M.gguf"]) is None


def _fake_quantize(calls):
    def q(src, dst, ftype, requantize):
        calls.append((Path(src).name, Path(dst).relative_to(Path(dst).parents[1]).as_posix(), ftype, requantize))
        Path(dst).write_bytes(b"q")
        return 0
    return q


def test_make_quants_requantizes_shipped_file_when_no_source(tmp_path):
    shipped = tmp_path / "models" / "Hy-MT2-1.8B-Q4_K_M.gguf"
    shipped.parent.mkdir()
    shipped.write_bytes(b"x")
    qd = tmp_path / "q"
    (qd / "Hy-MT2-1.8B-IQ4_XS.gguf").parent.mkdir()
    (qd / "Hy-MT2-1.8B-IQ4_XS.gguf").write_bytes(b"x")  # already downloaded: kept, not re-made
    calls = []
    got = _bench().make_quants(qd, ["IQ4_XS", "Q3_K_M", "Q4_0", "Q9_X"], str(shipped), _fake_quantize(calls))
    assert calls == [("Hy-MT2-1.8B-Q4_K_M.gguf", "self-from-Q4_K_M/Hy-MT2-1.8B-Q3_K_M.gguf", 12, True),
                     ("Hy-MT2-1.8B-Q4_K_M.gguf", "self-from-Q4_K_M/Hy-MT2-1.8B-Q4_0.gguf", 2, True)]
    assert got["IQ4_XS"] == str(qd / "Hy-MT2-1.8B-IQ4_XS.gguf") and got["Q9_X"] is None
    # the bench then finds the made files, and labels them with their source
    found = _bench().local_quants(qd, ["Q3_K_M"])["Q3_K_M"]
    assert _bench().quant_label("Q3_K_M", found) == "Q3_K_M (self from Q4_K_M)"
    assert _bench().quant_label("IQ4_XS", got["IQ4_XS"]) == "IQ4_XS"


def test_make_quants_uses_f16_source_without_requantize(tmp_path):
    qd = tmp_path / "q"
    (qd / "source").mkdir(parents=True)
    (qd / "source" / "Hy-MT2-1.8B-F16.gguf").write_bytes(b"x")
    calls = []
    got = _bench().make_quants(qd, ["Q3_K_S"], str(tmp_path / "missing.gguf"), _fake_quantize(calls))
    assert calls == [("Hy-MT2-1.8B-F16.gguf", "self-from-F16/Hy-MT2-1.8B-Q3_K_S.gguf", 11, False)]
    assert got == {"Q3_K_S": str(qd / "self-from-F16" / "Hy-MT2-1.8B-Q3_K_S.gguf")}
    # the source file itself is never offered as a bench quant
    assert _bench().local_quants(qd, ["F16"]) == {"F16": None}


def test_make_quants_failure_is_none(tmp_path):
    got = _bench().make_quants(tmp_path, ["Q2_K"], str(tmp_path / "x.gguf"), lambda *a: 1)
    assert got == {"Q2_K": None}


def test_make_quants_needs_quant_dir():
    with pytest.raises(SystemExit):
        _bench().main(["--make-quants", "--quants", "Q3_K_M"])


def test_ftypes_match_llama_h():
    """The ftype numbers are llama.h's enum; a wrong one would silently bench another quant."""
    llama_cpp = pytest.importorskip("llama_cpp")
    for q, v in _bench().FTYPES.items():
        assert getattr(llama_cpp, f"LLAMA_FTYPE_MOSTLY_{q}") == v, q
