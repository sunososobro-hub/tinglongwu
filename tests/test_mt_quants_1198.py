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
