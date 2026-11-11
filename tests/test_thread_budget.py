"""#250: one CPU thread budget for listen+speak mode (Whisper, llama.cpp / OPUS, TTS), listen-only unchanged."""
import sys
import types

import pytest

from tinglongwu import engines, llamacheck, threads

BOTH = {"TLWU_BOTH": "1"}


@pytest.mark.parametrize("n", [4, 8, 16])
def test_thread_budget_listen_speak_sum_within_budget(n):
    env = {**BOTH, "TLWU_CPU_THREADS": str(n)}
    li, sp = threads.plan("listen", env), threads.plan("speak", env)
    assert min(li["asr"], li["mt"], sp["asr"], sp["mt"], sp["tts"]) >= 1
    # concurrent pools: listen ASR, listen MT, speak ASR->MT (same thread), speak TTS
    assert li["asr"] + li["mt"] + sp["asr"] + sp["tts"] <= n
    assert threads.concurrent(threads.split(n)) == n
    assert li["mt_batch"] == li["mt"] and sp["mt_batch"] == sp["mt"]  # prompt processing no longer takes all CPUs


def test_thread_budget_numbers():
    assert threads.split(4) == {"listen": {"asr": 1, "mt": 1}, "speak": {"asr": 1, "mt": 1, "tts": 1}}
    assert threads.split(8) == {"listen": {"asr": 2, "mt": 2}, "speak": {"asr": 3, "mt": 3, "tts": 1}}
    assert threads.split(16) == {"listen": {"asr": 4, "mt": 4}, "speak": {"asr": 6, "mt": 6, "tts": 2}}


def test_thread_budget_from_cpu_count_or_env():
    assert threads.total({}, count=12) == 12
    assert threads.total({"TLWU_CPU_THREADS": "6"}, count=12) == 6
    assert threads.total({"TLWU_CPU_THREADS": "x"}, count=12) == 12
    assert threads.plan("listen", BOTH, count=8) == threads.plan("listen", {**BOTH, "TLWU_CPU_THREADS": "8"}, count=32)


@pytest.mark.parametrize("n", [4, 8, 16])
def test_thread_budget_listen_only_not_below_before(n):
    """Listen-only keeps what the libraries used before #250: Whisper 4, llama.cpp cpu//2 decode, cpu batch."""
    p = threads.plan("listen", {}, count=n)
    assert p["asr"] >= 4 and p["mt"] >= max(1, n // 2) and p["mt_batch"] >= n
    assert p == threads.defaults("listen", {}, count=n)


def test_thread_budget_speak_only_keeps_186_whisper_threads():
    from tinglongwu.device import cpu_threads

    assert threads.plan("speak", {}, count=16)["asr"] == cpu_threads(16, {}) == 8


def test_thread_budget_log_line_is_greppable():
    line = threads.log_line("speak", {**BOTH, "TLWU_CPU_THREADS": "8"}, count=8)  # before: #186 honoured it too
    assert line == "tlwu: threads role=speak budget=8 before=asr=8,mt=4/8,tts=4 after=asr=3,mt=3/3,tts=1"
    assert threads.log_line("listen", {}, count=8).endswith("before=asr=4,mt=4/8,tts=- after=asr=4,mt=4/8,tts=-")


def _fake_llama(made):
    m = types.ModuleType("llama_cpp")
    m.__file__ = "nowhere/llama_cpp/__init__.py"
    m.__version__ = "0.3.36"
    m.llama_print_system_info = lambda: b""
    m.llama_supports_gpu_offload = lambda: False
    m.Llama = lambda **kw: made.append(kw) or "llm"
    return m


@pytest.mark.parametrize("both, want", [(False, {"n_threads": 4, "n_threads_batch": 8}),
                                        (True, {"n_threads": 2, "n_threads_batch": 2})])
def test_load_llama_passes_explicit_threads(monkeypatch, tmp_path, both, want):
    made: list = []
    monkeypatch.setitem(sys.modules, "llama_cpp", _fake_llama(made))
    monkeypatch.setattr(llamacheck, "check_build", lambda mod: None)
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 8)
    monkeypatch.delenv("TLWU_CPU_THREADS", raising=False)
    monkeypatch.setenv("TLWU_BOTH", "1") if both else monkeypatch.delenv("TLWU_BOTH", raising=False)
    monkeypatch.setattr(threads, "_role", "listen")
    assert engines.load_llama(tmp_path / "x.gguf", gpu=False) == "llm"
    assert {k: made[0][k] for k in want} == want


@pytest.mark.parametrize("both, fast, want", [(True, False, 3), (False, True, 7), (False, False, None)])
def test_whisper_cpu_threads_follow_budget(monkeypatch, tmp_path, both, fast, want):
    import faster_whisper

    from tinglongwu import asr

    seen: list = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *a, **kw: seen.append(kw) or object())
    (tmp_path / "whisper-small").mkdir()
    monkeypatch.setattr(threads.os, "cpu_count", lambda: 8)
    monkeypatch.setenv("TLWU_CPU_THREADS", "8" if both else "7")
    monkeypatch.setenv("TLWU_BOTH", "1") if both else monkeypatch.delenv("TLWU_BOTH", raising=False)
    monkeypatch.setattr(threads, "_role", "speak")
    asr.FasterWhisperRecognizer(tmp_path, "small", "zh", "cpu", fast=fast)
    assert seen[0].get("cpu_threads") == want


def test_opus_and_tts_threads_only_in_listen_speak_mode(monkeypatch):
    monkeypatch.setenv("TLWU_CPU_THREADS", "16")
    monkeypatch.setattr(threads, "_role", "speak")
    monkeypatch.delenv("TLWU_BOTH", raising=False)
    assert threads.ct2_kwargs("cpu") == {} and threads.tts_env() == {}
    monkeypatch.setenv("TLWU_BOTH", "1")
    assert threads.ct2_kwargs("cpu") == {"intra_threads": 6} and threads.ct2_kwargs("cuda") == {}
    assert threads.tts_env() == {"TLWU_TTS_THREADS": "2", "OMP_NUM_THREADS": "2"}


def test_tts_client_hands_thread_count_to_worker(monkeypatch):
    from tinglongwu.reverse import tts_client

    seen: dict = {}

    class P:
        def __init__(self, cmd, env, **kw):
            seen.update(env)
            self.stdout = types.SimpleNamespace(readline=lambda: b'{"t": "ready"}\n')

    monkeypatch.setattr(tts_client.subprocess, "Popen", P)
    monkeypatch.setenv("TLWU_BOTH", "1")
    monkeypatch.setenv("TLWU_CPU_THREADS", "8")
    monkeypatch.setattr(threads, "_role", "speak")
    tts_client.TTSClient("py", "m", engine="kokoro")
    assert seen["TLWU_TTS_THREADS"] == "1" and seen["TLWU_MODE"] == "worker"


def test_kokoro_session_uses_tts_threads(monkeypatch):
    from tinglongwu.reverse import tts_worker

    ort = types.ModuleType("onnxruntime")

    class SO:
        intra_op_num_threads = inter_op_num_threads = 0

    ort.SessionOptions = SO
    ort.InferenceSession = lambda path, so, providers: ("sess", so.intra_op_num_threads, so.inter_op_num_threads)
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)

    class K:
        def __init__(self, onnx, voices):
            self.how = ("plain", onnx)

        @classmethod
        def from_session(cls, sess, voices):
            k = cls.__new__(cls)
            k.how = sess
            return k

    monkeypatch.delenv("TLWU_TTS_THREADS", raising=False)
    assert tts_worker._kokoro(K, "k.onnx", "v.bin").how == ("plain", "k.onnx")
    monkeypatch.setenv("TLWU_TTS_THREADS", "2")
    assert tts_worker._kokoro(K, "k.onnx", "v.bin").how == ("sess", 2, 1)


def test_set_role_prints_one_line_and_rejects_unknown_roles(monkeypatch, capsys):
    monkeypatch.setattr(threads, "_role", None)
    threads.set_role("listen")
    assert capsys.readouterr().err.startswith("tlwu: threads role=listen budget=")
    with pytest.raises(ValueError):
        threads.set_role("gui")
