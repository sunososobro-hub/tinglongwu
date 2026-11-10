"""Issue #74: an llama-cpp-python build compiled for AVX-512 on a CPU without it must give a clear error
(not a bare 'Windows Error 0xc000001d'), and the resulting fallback to OPUS must be announced loudly."""
import sys
import types

import pytest

from tinglongwu import cli, llamacheck, qwen
from tinglongwu.llamacheck import LlamaBuildError

# real llama_print_system_info() output of the two 0.3.36 Windows wheels (cu125 = bad on AVX2-only CPUs)
INFO_AVX512 = (b"CUDA : ARCHS = 600,610,700,750,800,860,890,900 | FORCE_MMQ = 1 | USE_GRAPHS = 1 | "
               b"FA_QUANTS = q4_0-q4_0,q8_0-q8_0,f16-f16,bf16-bf16 | CPU : SSE3 = 1 | SSSE3 = 1 | AVX = 1 | "
               b"AVX2 = 1 | F16C = 1 | FMA = 1 | AVX512 = 1 | LLAMAFILE = 1 | OPENMP = 1 | REPACK = 1 | ")
INFO_AVX2 = INFO_AVX512.replace(b"AVX512 = 1 | ", b"")
CPU_AVX2 = {"AVX": True, "AVX2": True, "AVX512": False}  # e.g. Intel i5-13500
CPU_AVX512 = {"AVX": True, "AVX2": True, "AVX512": True}
ILLEGAL = OSError(22, "Windows Error 0xc000001d", None, -1073741795)


def fake_llama(info, llama_cls=None, gpu=True):
    m = types.ModuleType("llama_cpp")
    m.__file__ = "nowhere/llama_cpp/__init__.py"
    m.__version__ = "0.3.36"
    m.llama_print_system_info = lambda: info
    m.llama_supports_gpu_offload = lambda: gpu
    m.Llama = llama_cls or (lambda **kw: ("loaded", kw))
    return m


def test_build_features_reads_only_the_cpu_section():
    f = llamacheck.build_features(INFO_AVX512.decode())
    assert {"AVX", "AVX2", "FMA", "AVX512"} <= f and "FORCE_MMQ" not in f
    assert "AVX512" not in llamacheck.build_features(INFO_AVX2.decode())
    assert llamacheck.build_features("AVX = 1 | AVX2 = 0 | AVX512_VNNI = 1 | ") == {"AVX", "AVX512_VNNI"}


def test_unsupported_features():
    assert llamacheck.unsupported({"AVX", "AVX2", "AVX512", "AVX512_VNNI"}, CPU_AVX2) == ["AVX512", "AVX512_VNNI"]
    assert llamacheck.unsupported({"AVX", "AVX2", "AVX512"}, CPU_AVX512) == []
    assert llamacheck.unsupported({"AVX", "AVX2", "FMA"}, CPU_AVX2) == []  # FMA unknown on Windows: no claim
    assert llamacheck.unsupported({"AVX512"}, None) == []  # CPU unknown: let the real load decide


def test_check_build_rejects_avx512_build_on_avx2_cpu_with_fix_hint():
    with pytest.raises(LlamaBuildError) as ei:
        llamacheck.check_build(fake_llama(INFO_AVX512), cpu=lambda: CPU_AVX2)
    msg = str(ei.value)
    assert "AVX512" in msg and "pip install --force-reinstall" in msg and "/whl/cu124" in msg
    llamacheck.check_build(fake_llama(INFO_AVX512), cpu=lambda: CPU_AVX512)  # matching CPU: fine
    llamacheck.check_build(fake_llama(INFO_AVX2), cpu=lambda: CPU_AVX2)      # the pinned build: fine


def test_cpu_features_on_this_machine():
    f = llamacheck.cpu_features()
    assert f is None or (f and all(isinstance(v, bool) for v in f.values()))


def test_explain_translates_illegal_instruction_only():
    e = llamacheck.explain(ILLEGAL)
    assert isinstance(e, LlamaBuildError) and "illegal CPU instruction" in str(e) and "To fix" in str(e)
    other = OSError("CUDA out of memory")
    assert llamacheck.explain(other) is other


def test_load_llama_refuses_mismatched_build_before_touching_the_model(monkeypatch, tmp_path):
    made = []
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama(INFO_AVX512, lambda **kw: made.append(kw)))
    monkeypatch.setattr(llamacheck, "cpu_features", lambda: CPU_AVX2)
    with pytest.raises(LlamaBuildError, match="AVX512"):
        qwen._load_llama(tmp_path / "x.gguf", gpu=True)
    assert made == []  # never reached the native load that would crash


def test_load_llama_loads_with_a_matching_build(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama(INFO_AVX2))
    monkeypatch.setattr(llamacheck, "cpu_features", lambda: CPU_AVX2)
    tag, kw = qwen._load_llama(tmp_path / "x.gguf", gpu=False)
    assert tag == "loaded" and kw["n_gpu_layers"] == 0 and kw["model_path"].endswith("x.gguf")


def test_load_llama_explains_a_raw_illegal_instruction(monkeypatch, tmp_path):
    def boom(**kw):
        raise ILLEGAL

    # the flag check could not tell (CPU features unknown) and the native load crashed
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama(INFO_AVX512, boom))
    monkeypatch.setattr(llamacheck, "cpu_features", lambda: None)
    with pytest.raises(LlamaBuildError, match="illegal CPU instruction"):
        qwen._load_llama(tmp_path / "x.gguf", gpu=True)


def test_fallback_to_opus_is_one_loud_line_with_the_reason(monkeypatch, tmp_path):
    from tinglongwu.translate import PivotTranslator

    gguf = qwen.model_path(tmp_path, "qwen3-4b")
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(b"GGUF")

    def crash(path, gpu):
        raise llamacheck.explain(ILLEGAL)

    monkeypatch.setattr(qwen, "_load_llama", crash)
    said = []
    tr = cli.make_translator("qwen4b", tmp_path, "tw", "cuda", say=lambda m, **k: said.append(m),
                             choose=lambda *a: ("qwen3-4b", "requested"))
    assert isinstance(tr, PivotTranslator) or isinstance(tr.base, PivotTranslator)
    loud = [m for m in said if "WARNING" in m]
    assert len(loud) == 1
    assert "falling back to OPUS" in loud[0] and "qwen3-4b" in loud[0]
    assert "0xc000001d" in loud[0] and "pip install --force-reinstall" in loud[0]  # why + how to fix
    assert "\n" not in loud[0]
    assert any("engine = opus" in m for m in said)


def test_tiers_report_their_errors(tmp_path):
    for n in ("qwen3-8b", "qwen3-4b"):
        p = qwen.model_path(tmp_path, n)
        p.parent.mkdir(parents=True)
        p.write_bytes(b"GGUF")

    class Fac(qwen.QwenTranslator):
        def __init__(self, md, name, zh, dev):
            super().__init__(md, name, zh, dev, loader=self.load)

        def load(self, path, gpu):
            raise ValueError(f"boom {self.name}")

    warns, errors = [], []
    assert qwen.build_qwen_with_fallback(tmp_path, "qwen3-8b", "tw", "cuda", warn=warns.append, factory=Fac,
                                         errors=errors) == (None, None)
    assert errors == ["qwen3-8b: boom qwen3-8b", "qwen3-4b: boom qwen3-4b"]
    assert len(warns) == 1 and "trying qwen3-4b" in warns[0]  # the last tier is reported by the caller


def test_self_check_and_setup_exit_code(monkeypatch, capsys):
    ok, msg = llamacheck.self_check(importer=lambda: fake_llama(INFO_AVX2), cpu=lambda: CPU_AVX2)
    assert ok and "OK" in msg
    ok, msg = llamacheck.self_check(importer=lambda: fake_llama(INFO_AVX512), cpu=lambda: CPU_AVX2)
    assert not ok and "AVX512" in msg and "To fix" in msg
    ok, msg = llamacheck.self_check(gpu=True, importer=lambda: fake_llama(INFO_AVX2, gpu=False),
                                    cpu=lambda: CPU_AVX2)
    assert not ok and "CPU-only" in msg

    def no_dll():
        raise RuntimeError("Failed to load shared library llama.dll")

    ok, msg = llamacheck.self_check(importer=no_dll)
    assert not ok and "llama.dll" in msg and "To fix" in msg

    monkeypatch.setattr(llamacheck, "self_check", lambda gpu=False: (False, "bad build"))
    assert llamacheck.main([]) == 1
    assert "bad build" in capsys.readouterr().err
    monkeypatch.setattr(llamacheck, "self_check", lambda gpu=False: (True, "fine"))
    assert llamacheck.main([]) == 0

    from tinglongwu_setup import cli as setup_cli

    ran = []
    monkeypatch.setattr(setup_cli.subprocess, "call", lambda cmd: ran.append(cmd) or 1)
    assert setup_cli.main(["--check-llama"]) == 1  # exit code of the check is passed through
    assert ran == [[sys.executable, "-m", "tinglongwu.llamacheck"]]
