"""CPU thread budget (#250): one function decides how many threads Whisper, the MT engine and TTS get.

Listen+speak mode (the menus set TLWU_BOTH=1 for both processes) runs two processes side by side, and every library
picks its own default: faster-whisper 4 (the speak side all physical cores, #186), llama.cpp half the logical CPUs
for decode plus ALL of them for prompt processing, onnxruntime one per physical core. Together that is far more
threads than cores. In that mode the budget (TLWU_CPU_THREADS, else the logical CPU count) is split over the four
pools that actually run at the same time:

  listen ASR   listen MT   (listen pipeline: ASR producer and MT worker are separate threads)
  speak ASR+MT             (speak side: ASR then MT in the same thread, so they share one slot)
  speak TTS                (worker process, overlaps the next sentence)

TTS gets 1/8 of the budget (short bursts), the rest is split in three with the remainder going to the speak side
(the user waits on it). Below 4 threads every pool still gets 1.

Listen-only and speak-only keep the libraries' defaults (now passed explicitly to llama.cpp), so nothing there
gets fewer threads than before.
"""
from __future__ import annotations

import os
import sys

ROLES = ("listen", "speak")
_role: str | None = None


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
            "tts": max(1, n // 2) if speak else 0}


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
    _role = role
    print(log_line(role), file=out or sys.stderr, flush=True)


def current() -> dict:
    """Plan of this process (listen when no entry point set a role, e.g. the GUI before run_session)."""
    return plan(_role or "listen")


def ct2_kwargs(device: str) -> dict:
    """Extra ctranslate2.Translator kwargs for an OPUS model: only in listen+speak mode on the CPU."""
    return {"intra_threads": current()["mt"]} if device == "cpu" and both() else {}


def tts_env() -> dict:
    """Env for the TTS worker process: in listen+speak mode, its onnxruntime thread count."""
    if not both():
        return {}
    n = str(current()["tts"] or 1)
    return {"TLWU_TTS_THREADS": n, "OMP_NUM_THREADS": n}
