"""#1773: run the CPU vs Vulkan speed check (core/vulkan_probe.py decides and caches).

One child process per backend, because one process can load only one llama.cpp build (#746): the child is
`tlwu --time-backend cpu|vulkan --model <size> --models-dir <dir>`; it loads Whisper + Hy-MT2 on that backend, warms
up on the bundled clip, then prints one JSON line with the best of TIMED_RUNS timed ASR + MT passes. The parent
kills a child after CHILD_TIMEOUT_S (a timeout keeps Vulkan, owner rule).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

CHILD_TIMEOUT_S = 240  # model load + first Vulkan shader compile on a slow iGPU; a timeout only means "keep Vulkan"
TIMED_RUNS = 2  # best of 2 after one warm-up pass: one-off stalls (page cache, shader cache) do not decide
RESULT_PREFIX = "tlwu-time-backend: "


def child_cmd(backend: str, size: str, mdir, bundled: bool | None = None) -> list[str]:
    if bundled is None:
        bundled = bool(getattr(sys, "frozen", False)) or Path(sys.executable).name.lower() == "tlwu.exe"
    base = [os.path.abspath(sys.argv[0])] if bundled else [sys.executable, "-m", "tinglongwu"]
    return [*base, "--time-backend", backend, "--model", size, "--models-dir", str(mdir)]


def parse_child(stdout: str) -> float:
    """Seconds (ASR + MT) from the child's result line; raises RuntimeError when it is missing."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            d = json.loads(line[len(RESULT_PREFIX):])
            return float(d["asr_s"]) + float(d["mt_s"])
    raise RuntimeError("no timing from the child: " + (stdout.strip().splitlines() or ["(no output)"])[-1][:120])


class ProbeStopped(RuntimeError):
    """The session was stopped while a child was being timed (#1783): no result, no cache entry."""


def _run_stoppable(cmd, env, stop, timeout, poll: float = 0.2):
    """Like subprocess.run(capture_output, text) but kills the child within `poll` s once `stop` is set."""
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    end = time.monotonic() + timeout
    try:
        while True:
            try:
                out, err = p.communicate(timeout=poll)
            except subprocess.TimeoutExpired:
                out = None
            if out is not None:
                if stop is not None and stop.is_set():  # #1791: the stop landed as the child exited: no result
                    raise ProbeStopped("session stopped during the GPU speed check")
                return types.SimpleNamespace(returncode=p.returncode, stdout=out, stderr=err)
            if stop is not None and stop.is_set():
                raise ProbeStopped("session stopped during the GPU speed check")
            if time.monotonic() >= end:
                raise subprocess.TimeoutExpired(cmd, timeout)
    except BaseException:  # #1791: Ctrl+C, stop or timeout: never leave the child running
        if p.poll() is None:
            p.kill()
        try:
            p.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        raise


def time_in_child(backend: str, size: str, mdir, *, run=None, timeout: float = CHILD_TIMEOUT_S, stop=None) -> float:
    env = dict(os.environ, TLWU_DEVICE=backend)
    cmd = child_cmd(backend, size, mdir)
    if run is not None:
        p = run(cmd, env=env, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    else:
        p = _run_stoppable(cmd, env, stop, timeout)
    if p.returncode != 0:
        tail = ((p.stderr or "").strip().splitlines() or [f"exit {p.returncode}"])[-1]
        raise RuntimeError(tail[:160])
    return parse_child(p.stdout or "")


def time_here(backend: str, mdir, size: str, *, make_asr=None, make_mt=None, clip=None, clock=time.perf_counter,
              out=print) -> dict:
    """Child body: load both stages on `backend`, warm up, time. Raises when Vulkan was asked but not used."""
    from . import asr_vulkan, mt_backend
    from .selftest import CLIP_TEXT, load_clip

    mdir = Path(mdir)
    audio = (clip or load_clip)()
    if make_asr is None:
        def make_asr():
            if backend == "vulkan" and asr_vulkan.vulkan_ready(mdir, size):
                return asr_vulkan.WhisperCppVulkanRecognizer(mdir, size, "en"), "vulkan"
            from .asr import FasterWhisperRecognizer

            return FasterWhisperRecognizer(mdir, size, "en", "cpu"), "cpu"
    if make_mt is None:
        def make_mt():
            from .hymt import HyMtTranslator

            tr = HyMtTranslator(mdir, device="cpu")  # load_llama picks Vulkan under TLWU_DEVICE=vulkan
            tr.preload()
            return tr, mt_backend.last_backend
    rec, asr_be = make_asr()
    tr, mt_be = make_mt()
    if backend == "vulkan" and mt_be != "vulkan":  # #1783: MT silently on the CPU would time a CPU run as "Vulkan"
        raise RuntimeError("Vulkan did not load for MT" if asr_be == "vulkan" else "Vulkan did not load for ASR or MT")
    best = None
    for i in range(1 + TIMED_RUNS):
        t0 = clock()
        rec.transcribe(audio)
        t1 = clock()
        tr.translate(CLIP_TEXT, "en", "zh")
        t2 = clock()
        if i and (best is None or (t2 - t0) < best[0] + best[1]):
            best = (t1 - t0, t2 - t1)
    d = {"backend": backend, "asr": asr_be, "mt": mt_be, "asr_s": round(best[0], 3), "mt_s": round(best[1], 3)}
    out(RESULT_PREFIX + json.dumps(d))
    return d


def maybe_probe(a, mdir, dev: str, *, env=None, say=None, available=None, timer=None, path=None, stop=None):
    """Session start: run (or reuse) the speed check when Vulkan is a candidate: no CUDA, not --device cpu, the
    Vulkan pack usable. Returns the ProbeResult, or None when no check applies."""
    from . import mt_backend, vulkan_probe

    env = os.environ if env is None else env
    say = say or (lambda m: print(m, file=sys.stderr, flush=True))
    if dev == "cuda" or getattr(a, "compute", "auto") == "cpu":
        return None
    try:
        if not (available or mt_backend.vulkan_available)():
            return None
        key = vulkan_probe.cache_key(mt_backend.vulkan_pack_dir(warn=lambda _m: None))
    except Exception:  # noqa: BLE001 - no probe is never a reason to fail a session
        return None
    hit = None if vulkan_probe.forced(env) else vulkan_probe.load(key, path)
    if hit is not None:
        vulkan_probe.remember(key, hit.winner)  # #1791: a cache hit is this session's decision too
        say(f"{hit.note} (remembered)")
        return hit
    if not vulkan_probe.forced(env):
        say("tlwu: GPU speed check CPU vs Vulkan (once after install, update or driver change)...")
    return vulkan_probe.ensure(timer or (lambda b: time_in_child(b, a.model, mdir, stop=stop)), key, env=env, path=path, say=say)
