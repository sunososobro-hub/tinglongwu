"""CPU thread budget (#250): one function decides how many threads Whisper, the MT engine and TTS get.

Listen+speak mode (TLWU_BOTH=1; since #285 one process: `reverse.proto --listen`, listen side in a thread) runs
both sides at once, and every library picks its own default: faster-whisper 4 (the speak side all physical cores,
#186), llama.cpp half the logical CPUs for decode plus ALL of them for prompt processing, onnxruntime one per physical core. Together that is far more
threads than cores. In that mode the budget (TLWU_CPU_THREADS, else the logical CPU count) is split over the four
pools that actually run at the same time:

  listen ASR   listen MT   (listen pipeline: ASR producer and MT worker are separate threads)
  speak ASR+MT             (speak side: ASR then MT in the same thread, so they share one slot)
  speak TTS                (worker process, overlaps the next sentence)

TTS gets 1/8 of the budget (short bursts), the rest is split in three with the remainder going to the speak side
(the user waits on it). Below 4 threads every pool still gets 1.

Listen-only and speak-only keep the libraries' defaults (now passed explicitly to llama.cpp), so nothing there
gets fewer threads than before. Exception (#207): the speak-only TTS worker gets min(8, logical CPUs // 2)
onnxruntime threads instead of onnxruntime's one per physical core. On hybrid CPUs that default includes the
E-cores and is slower (Kokoro int8, i5-13500 6P+8E: 14 threads 1.8 s vs 8 threads 0.9 s for a 40-char piece).
"""
from __future__ import annotations

import os
import sys
import threading

ROLES = ("listen", "speak")
_role: str | None = None
_local = threading.local()  # #285: listen+speak in one process, each side in its own thread


def total(env=None, count=None) -> int:
    """The thread budget: TLWU_CPU_THREADS when set to a positive integer, else the logical CPU count."""
    env = os.environ if env is None else env
    raw = (env.get("TLWU_CPU_THREADS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return count if count is not None else (os.cpu_count() or 4)


def both(env=None) -> bool:
    """True in listen+speak mode (the menus export TLWU_BOTH=1 to both processes)."""
    return (os.environ if env is None else env).get("TLWU_BOTH") == "1"


def defaults(role: str, env=None, count=None) -> dict:
    """What each library uses when nothing is set (faster-whisper 4, the speak side's #186 cpu_threads(),
    llama-cpp-python cpu_count//2 and cpu_count, onnxruntime ~physical cores)."""
    from .device import cpu_threads

    n = count if count is not None else (os.cpu_count() or 4)
    speak = role == "speak"
    return {"asr": cpu_threads(n, env) if speak else 4, "mt": max(1, n // 2), "mt_batch": n,
            "tts": speak_tts_threads(n) if speak else 0}


TTS_MAX_THREADS = 8  # #207: more onnxruntime threads than this made Kokoro slower, not faster (bench in threads doc)


def speak_tts_threads(n: int) -> int:
    """Speak-only TTS worker threads for `n` logical CPUs (#207)."""
    return max(1, min(TTS_MAX_THREADS, n // 2))


def split(n: int) -> dict:
    """Listen+speak budget of `n` threads -> {"listen": {asr, mt}, "speak": {asr, mt, tts}}.
    speak asr and mt run one after the other, so together they use one slot."""
    tts = max(1, n // 8)
    rest = max(3, n - tts)
    listen = rest // 3
    speak = rest - 2 * listen
    return {"listen": {"asr": listen, "mt": listen}, "speak": {"asr": speak, "mt": speak, "tts": tts}}


def concurrent(sp: dict) -> int:
    """Threads that can be busy at the same time for a split() result."""
    return sp["listen"]["asr"] + sp["listen"]["mt"] + sp["speak"]["asr"] + sp["speak"]["tts"]


def plan(role: str, env=None, count=None) -> dict:
    """Threads per component for this process: {asr, mt, mt_batch, tts} (tts 0 = this process has no TTS)."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if not both(env):
        return defaults(role, env, count)
    s = split(total(env, count))[role]
    return {"asr": s["asr"], "mt": s["mt"], "mt_batch": s["mt"], "tts": s.get("tts", 0)}


def _fmt(p: dict) -> str:
    return f"asr={p['asr']},mt={p['mt']}/{p['mt_batch']},tts={p['tts'] or '-'}"


def log_line(role: str, env=None, count=None) -> str:
    """One greppable startup line: `threads role=<r> budget=<n> before=asr=..,mt=../..,tts=.. after=...`."""
    return (f"tlwu: threads role={role} budget={total(env, count)} "
            f"before={_fmt(defaults(role, env, count))} after={_fmt(plan(role, env, count))}")


def set_role(role: str, out=None) -> None:
    """Called once by each entry point (cli: listen, reverse.proto: speak); prints the threads line."""
    global _role
    plan(role)  # validates the role
    main = threading.current_thread() is threading.main_thread()
    if not main:
        _local.role = role  # e.g. the listen side started by `reverse.proto --listen` (#285)
    if main or _role is None:
        _role = role  # threads without a role of their own (MT worker, drafts) use the main thread's
    print(log_line(role), file=out or sys.stderr, flush=True)


def current() -> dict:
    """Plan of this process (listen when no entry point set a role, e.g. the GUI before run_session)."""
    own = None if threading.current_thread() is threading.main_thread() else getattr(_local, "role", None)
    return plan(own or _role or "listen")


def ct2_kwargs(device: str) -> dict:
    """Extra ctranslate2.Translator kwargs for an OPUS model: only in listen+speak mode on the CPU."""
    return {"intra_threads": current()["mt"]} if device == "cpu" and both() else {}


def tts_env(env=None) -> dict:
    """Env for the TTS worker process: its onnxruntime thread count. Listen+speak mode: the budget's share.
    Speak-only (#207): speak_tts_threads(), unless TLWU_TTS_THREADS is already set (then the worker reads that)."""
    env = os.environ if env is None else env
    if not both(env):
        if (env.get("TLWU_TTS_THREADS") or "").strip():
            return {}
        n = str(speak_tts_threads(total(env)))
        return {"TLWU_TTS_THREADS": n, "OMP_NUM_THREADS": n}
    n = str(current()["tts"] or 1)
    return {"TLWU_TTS_THREADS": n, "OMP_NUM_THREADS": n}
