#!/usr/bin/env python3
"""Hosted-runner end-to-end checks for issues that used to wait on a hands-on test (status:needs-test).

Used by .github/workflows/e2e.yml (ubuntu / windows / macos GitHub-hosted runners: no GPU, no microphone, no
speakers). Real models (smoke set: Whisper base + Hy-MT2 1.8B on the CPU + OPUS zh-en + Kokoro), real processes,
real audio files; Linux additionally a real PulseAudio server with a null sink. Each subcommand prints one
PASS/FAIL line per check and exits 0 only when every check passed.

    python scripts/ci_e2e.py zh-wav OUT.wav [--repeat N]       Chinese test speech (tests/fixtures) as 16 kHz wav
    python scripts/ci_e2e.py ctrlc --mode listen|speak ...      #5  real Ctrl+C (Windows: CTRL_C_EVENT) -> no traceback
    python scripts/ci_e2e.py gpu-old                            #89/#90 nvidia-smi sees an old-driver GPU -> advice
    python scripts/ci_e2e.py doctor --to ru                     #313 every problem line carries a fix
    python scripts/ci_e2e.py speak-ru                           #311 Russian voice after tlwu-setup --vosk-tts-ru
    python scripts/ci_e2e.py modes --listen-wav W --speak-wav Z #8  listen / speak / meeting on audio files
    python scripts/ci_e2e.py monitor-live ...                   #192/#186 speak mode on a PulseAudio "Monitor of"
    python scripts/ci_e2e.py overlay-stack                      #322 bar above a fullscreen window (X11 + WM)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZH_SENTENCES = ("今天天氣很好，我們去公園散步吧。", "請問會議幾點開始？", "這個問題我們明天再討論。")
CLIP_EN = ROOT / "tinglongwu" / "data" / "selftest_en.wav"
WIN = sys.platform == "win32"
PY = sys.executable
# unbuffered: readiness is detected from the child's output as it happens; faulthandler: native crash -> stack
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "TLWU_NO_UPDATE": "1", "PYTHONUNBUFFERED": "1",
       "PYTHONFAULTHANDLER": "1"}
CLEAN_RC = (0, 130, -2, 3221225786)  # done, KeyboardInterrupt handled (130 / SIGINT), STATUS_CONTROL_C_EXIT
MT = os.environ.get("TLWU_E2E_MT", "auto")  # speak-mode engine (auto = what a user gets)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def finish() -> int:
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)}/{len(results)} checks passed" + (f"; failed: {bad}" if bad else ""))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            for n, ok, d in results:
                f.write(f"- {'PASS' if ok else 'FAIL'} **{n}** {d.replace(chr(10), ' ')[:300]}\n")
    return 1 if bad else 0


def tail(text: str, n: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def whisper_args() -> list[str]:
    w = os.environ.get("TLWU_E2E_WHISPER", "")  # default: what speak mode picks itself (small on the CPU)
    return ["--whisper", w] if w else []


def kokoro_args() -> list[str]:
    py, model = os.environ.get("TLWU_KOKORO_PYTHON", ""), os.environ.get("TLWU_KOKORO_MODEL", "")
    if not (py and model):
        return ["--no-tts"]
    return ["--tts-engine", "kokoro", "--tts-python", py, "--tts-model", model]


# ---------------------------------------------------------------- test audio
ZH_CODE = (  # runs with the Kokoro venv's python: argv = model dir, out wav, sentences...
    "import sys, numpy as np, soundfile as sf\n"
    "from kokoro_onnx import Kokoro\n"
    "d = sys.argv[1]\n"
    "k = Kokoro(d + '/kokoro-v1.0.int8.onnx', d + '/voices-v1.0.bin')\n"
    "parts = []\n"
    "for t in sys.argv[3:]:\n"
    "    a, sr = k.create(t, voice='zf_xiaobei', speed=1.0, lang='cmn')\n"
    "    parts += [a, np.zeros(int(sr * 1.2), a.dtype)]\n"
    "sf.write(sys.argv[2], np.concatenate(parts), sr)\n")
_zh_cache: list = []


def zh_speech():
    """Chinese test speech as float32 mono 16 kHz: three sentences synthesized by Kokoro (zf_xiaobei, Apache-2.0)
    with the job's Kokoro venv, so no audio file has to live in the repo (*.wav / *.flac are git-ignored)."""
    if _zh_cache:
        return _zh_cache[0]
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    py, model = os.environ.get("TLWU_KOKORO_PYTHON", ""), os.environ.get("TLWU_KOKORO_MODEL", "")
    if not (py and model):
        sys.exit("TLWU_KOKORO_PYTHON / TLWU_KOKORO_MODEL must point at the Kokoro venv and model (tlwu-setup --talk)")
    with tempfile.TemporaryDirectory(prefix="tlwu-zh-") as td:
        raw = Path(td) / "zh24k.wav"
        subprocess.run([py, "-c", ZH_CODE, model, str(raw), *ZH_SENTENCES], check=True)
        x, sr = sf.read(str(raw), dtype="float32")
    if sr != 16000:
        from math import gcd

        g = gcd(sr, 16000)
        x = resample_poly(x, 16000 // g, sr // g).astype(np.float32)
    _zh_cache.append(x)
    return x


def zh_wav(out: Path, repeat: int = 1, gap_s: float = 1.0) -> Path:
    import numpy as np
    import soundfile as sf

    x, sr = zh_speech(), 16000
    gap = np.zeros(int(gap_s * sr), np.float32)
    sf.write(str(out), np.concatenate([np.concatenate([x, gap]) for _ in range(max(1, repeat))]), sr)
    return out


def cmd_zh_wav(a) -> int:
    zh_wav(Path(a.out), a.repeat)
    print(a.out)
    return 0


# ---------------------------------------------------------------- #5 Ctrl+C
def _wait_ready(p: subprocess.Popen, ready: re.Pattern, timeout: float, buf: list[str]) -> bool:
    """Pump p's merged output into buf; True once a line matches `ready`."""
    hit = threading.Event()

    def pump():
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            buf.append(line)
            if ready.search(line):
                hit.set()

    threading.Thread(target=pump, daemon=True).start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout and p.poll() is None and not hit.is_set():
        time.sleep(0.2)
    return hit.is_set()


def _ctrlc_cmd(mode: str, work: Path, models: str | None) -> tuple[list[str], re.Pattern]:
    md = ["--models-dir", models] if models else []
    if mode == "listen":
        en = work / "long_en.wav"
        import numpy as np
        import soundfile as sf

        x, sr = sf.read(str(CLIP_EN), dtype="float32")
        sf.write(str(en), np.concatenate([x] * 40), sr)  # still busy when Ctrl+C comes
        # the ready line comes once the models are loaded and the pipeline runs (stdout or stderr: both read here)
        return ([PY, "-m", "tinglongwu", "--file", str(en), "--to", "zh", "--device", "cpu", *md],
                re.compile(r"^\* ready - listening"))
    d = work / "zh"
    d.mkdir()
    for i in range(8):
        zh_wav(d / f"zh{i}.wav")
    return ([PY, "-m", "tinglongwu.reverse.proto", "--input", str(d), "--to", "en", "--compute", "cpu",
             "--mt", MT, "--no-play", "--no-vmic", "--no-confirm", "--out", str(work / "tts"), *md,
             *whisper_args(), *kokoro_args()], re.compile(r"^\[1/\d+\]"))


def _unload_vmic() -> None:
    """A killed speak process skips its atexit cleanup: unload its tlwu_mic / tlwu_sink modules (mic first)."""
    out = subprocess.run(["pactl", "list", "short", "modules"], capture_output=True, text=True).stdout
    rows = [ln.split("\t") for ln in out.splitlines()]
    for key in ("source_name=tlwu_mic", "sink_name=tlwu_sink"):
        for r in rows:
            if len(r) > 2 and key in r[2]:
                subprocess.run(["pactl", "unload-module", r[0]], check=False)


def _descendants(pid: int) -> list[int]:
    try:
        import psutil

        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except Exception:  # noqa: BLE001
        return []


def _alive(pids: list[int]) -> list[int]:
    try:
        import psutil

        return [p for p in pids if psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]
    except Exception:  # noqa: BLE001
        return []


def ctrlc_once(mode: str, models: str | None, ready_timeout: float, exit_timeout: float) -> dict:
    """Start the real program, wait until it is busy, deliver Ctrl+C the way a terminal does, report."""
    with tempfile.TemporaryDirectory(prefix="tlwu-ctrlc-") as td:
        cmd, ready = _ctrlc_cmd(mode, Path(td), models)
        kw = {} if WIN else {"start_new_session": True}  # POSIX: own process group, like a terminal job
        p = subprocess.Popen(cmd, cwd=str(ROOT), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        buf: list[str] = []
        busy = _wait_ready(p, ready, ready_timeout, buf)
        kids = _descendants(p.pid)
        t0 = time.monotonic()
        alive = p.poll() is None  # Ctrl+C must hit a running program, not one that already finished
        if alive:
            if WIN:
                os.kill(0, signal.CTRL_C_EVENT)  # every process on this console: the program and its children
            else:
                os.killpg(p.pid, signal.SIGINT)  # the whole process group, as the terminal does
        try:
            rc = p.wait(timeout=exit_timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = None
        secs = time.monotonic() - t0
        time.sleep(1.0)
        out = "\n".join(buf)
        return {"mode": mode, "busy": busy, "alive": alive, "rc": rc, "exit_s": round(secs, 1), "children": len(kids),
                "left_over": len(_alive(kids)), "traceback": "Traceback (most recent call last)" in out,
                "tail": tail(out, 15)}


def cmd_ctrlc(a) -> int:
    if WIN and not a.inner:
        # CTRL_C_EVENT reaches every process on the console, so run the test on a console of its own (the CI
        # step's shell must not get it); the inner run writes its verdict to a file.
        res = Path(tempfile.mkdtemp(prefix="tlwu-ctrlc-")) / "res.json"
        argv = [PY, str(Path(__file__).resolve()), "ctrlc", "--inner", "--result", str(res), "--mode", a.mode,
                "--ready-timeout", str(a.ready_timeout), "--exit-timeout", str(a.exit_timeout)]
        if a.models_dir:
            argv += ["--models-dir", a.models_dir]
        with open(res.with_suffix(".log"), "wb") as log:
            rc = subprocess.call(argv, env=ENV, creationflags=subprocess.CREATE_NO_WINDOW, stdout=log,
                                 stderr=subprocess.STDOUT)
        if not res.exists():
            print(tail(res.with_suffix(".log").read_text("utf-8", "replace"), 30))
            check(f"ctrl-c {a.mode}", False, f"inner run wrote no result (exit {rc})")
            return finish()
        r = json.loads(res.read_text(encoding="utf-8"))
    else:
        if WIN:
            import ctypes

            # a parent started with CREATE_NEW_PROCESS_GROUP (CI shells, MSYS) passes "Ctrl+C disabled" down to every
            # child; a real console does not, so turn Ctrl+C processing back on for us and what we start
            ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
            signal.signal(signal.SIGINT, lambda *_: None)  # the event reaches us too: ignore it here only
        r = ctrlc_once(a.mode, a.models_dir, a.ready_timeout, a.exit_timeout)
        if a.inner:
            Path(a.result).write_text(json.dumps(r), encoding="utf-8")
            return 0
    ok = (r["busy"] and r["alive"] and r["rc"] in CLEAN_RC and not r["traceback"] and not r["left_over"])
    check(f"ctrl-c {a.mode}", ok, f"busy={r['busy']} running at Ctrl+C={r['alive']} rc={r['rc']} exit {r['exit_s']} s, child processes "
          f"{r['children']} (left running: {r['left_over']}), traceback={r['traceback']}")
    if not ok:
        print(r["tail"])
    return finish()


# ---------------------------------------------------------------- #89 / #90 old NVIDIA driver
FAKE_SMI_LINE = "NVIDIA GeForce GTX 1050, 3072, 2900, 457.49"


FAKE_SMI_CS = r"""
using System;
class P { static int Main(string[] a) {
  string[] f = "%LINE%".Split(new[] {", "}, StringSplitOptions.None);
  string q = String.Join(" ", a); int i = q.IndexOf("--query-gpu=");
  if (i < 0) { Console.WriteLine("NVIDIA-SMI 457.49"); return 0; }
  string[] keys = q.Substring(i + 12).Split(' ')[0].Split(',');
  var o = new System.Collections.Generic.List<string>();
  foreach (var k in keys) o.Add(k == "name" ? f[0] : k == "memory.total" ? f[1] : k == "memory.free" ? f[2]
                                : k == "driver_version" ? f[3] : "");
  Console.WriteLine(String.Join(", ", o)); return 0; } }
"""


def fake_nvidia_smi(d: Path) -> Path:
    """nvidia-smi that reports a GTX 1050 with driver 457.49 (too old for CUDA 12), whatever it is asked.
    Windows: a real .exe (subprocess and PowerShell both resolve `nvidia-smi` to nvidia-smi.exe), built with the
    .NET Framework C# compiler that every Windows ships."""
    d.mkdir(parents=True, exist_ok=True)
    if WIN:
        src, exe = d / "smi.cs", d / "nvidia-smi.exe"
        src.write_text(FAKE_SMI_CS.replace("%LINE%", FAKE_SMI_LINE), encoding="ascii")
        windir = os.environ.get("WINDIR", r"C:\Windows")
        csc = next(Path(windir, "Microsoft.NET", fw, "v4.0.30319", "csc.exe") for fw in ("Framework64", "Framework")
                   if Path(windir, "Microsoft.NET", fw, "v4.0.30319", "csc.exe").exists())
        subprocess.run([str(csc), "/nologo", f"/out:{exe}", str(src)], check=True, capture_output=True)
        return exe
    code = (f"import sys; q=' '.join(sys.argv); f='{FAKE_SMI_LINE}'.split(', '); "
            "keys=(q.split('--query-gpu=')[1].split()[0].split(',') if '--query-gpu=' in q else []); "
            "m={'name':f[0],'memory.total':f[1],'memory.free':f[2],'driver_version':f[3]}; "
            "print(', '.join(m.get(k, '') for k in keys) if keys else 'NVIDIA-SMI 457.49')")
    f = d / "nvidia-smi"
    f.write_text(f'#!/bin/sh\nexec {PY} -c "{code}" "$@"\n', encoding="ascii")
    f.chmod(0o755)
    return f


def cmd_gpu_old(a) -> int:
    with tempfile.TemporaryDirectory(prefix="tlwu-smi-") as td:
        fake_nvidia_smi(Path(td))
        env = {**ENV, "PATH": td + os.pathsep + os.environ["PATH"], "TLWU_UI_LANG": "en"}
        md = ["--models-dir", a.models_dir] if a.models_dir else []
        probe = subprocess.run([PY, "-c", "from tinglongwu.device import driver_version as v; print(v())"],
                               env=env, capture_output=True, text=True, encoding="utf-8")
        check("fake nvidia-smi is the one tlwu sees", "457.49" in probe.stdout, probe.stdout.strip() or probe.stderr)
        # runtime (#89 item 3): the CPU fallback names the real reason, with the fix
        r = subprocess.run([PY, "-c", "from tinglongwu.device import resolve_device as r; "
                                      "print('device=' + r('auto', warn=lambda m: print('WARN', m)))"],
                           env=env, capture_output=True, text=True, encoding="utf-8")
        out = r.stdout + r.stderr
        check("runtime: CPU fallback says 'update the driver' (#89)",
              "device=cpu" in out and re.search(r"driver", out, re.I) is not None and "457.49" in out, tail(out, 4))
        if WIN:  # install time (#89 item 1): install.ps1 reads the driver version and says to update it
            r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(ROOT / "install.ps1"), "-DryRun", "-SkipShortcut"], env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=600)
            out = r.stdout + r.stderr
            hit = [ln.strip() for ln in out.splitlines() if "457.49" in ln and "too old" in ln]
            check("install.ps1: 'driver too old, update' with the link (#89)",
                  bool(hit) and "nvidia.com" in hit[0], (hit or [tail(out, 6)])[0])
        # install-time self-test (#90): GPU FAIL + driver advice, downgraded to the CPU tier, then PASS
        s = subprocess.run([PY, "-m", "tinglongwu", "--selftest", "--device", "auto", *md], env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        out = s.stdout + s.stderr
        print(out)
        check("selftest: GPU FAIL with driver advice (#90)",
              re.search(r"\[FAIL\] GPU", out) is not None and "update the NVIDIA graphics driver" in out)
        check("selftest: PASSED after downgrade on the CPU (#90)",
              s.returncode == 0 and "self-test PASSED after downgrade" in out and "device=cpu" in out,
              f"exit {s.returncode}")
    return finish()


def cmd_selftest(a) -> int:
    md = ["--models-dir", a.models_dir] if a.models_dir else []
    s = subprocess.run([PY, "-m", "tinglongwu", "--selftest", "--device", "cpu", *md], env=ENV, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    out = s.stdout + s.stderr
    print(out)
    m = re.search(r"total ([\d.]+) s", out)
    check("tlwu --selftest on the CPU (#90)", s.returncode == 0 and "self-test PASSED" in out
          and re.search(r"translation hymt2 on CPU \([\d.]+ s\): .*[一-鿿]", out) is not None,
          f"exit {s.returncode}, total {m.group(1) if m else '?'} s")
    return finish()


# ---------------------------------------------------------------- #313 doctor
def cmd_doctor(a) -> int:
    md = ["--models-dir", a.models_dir] if a.models_dir else []
    r = subprocess.run([PY, "-m", "tinglongwu", "doctor", "--to", a.to, *md], env=ENV, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = r.stdout
    print(out, r.stderr)
    lines = out.splitlines()
    status = [ln for ln in lines if re.match(r"\[\s*(ok|warn|FAIL)\]", ln)]
    check("doctor prints one line per check", len(status) >= 6 and "Traceback" not in r.stderr, f"{len(status)} lines")
    for i, ln in enumerate(lines):
        if re.match(r"\[\s*FAIL\]", ln):  # a FAIL must say what to do; a warn may be advice only (no GPU)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            check(f"doctor: fix given for '{ln.split(']')[1].split(':')[0].strip()}'", nxt.strip().startswith("fix:"),
                  ln.strip())
    for name in a.expect_ok:
        check(f"doctor: '{name}' is ok on this runner", any(re.match(rf"\[\s*ok\] {re.escape(name)}", ln)
                                                            for ln in lines))
    fails = sum(1 for ln in status if "FAIL" in ln)
    check("doctor exit code matches its FAIL lines", r.returncode == (1 if fails else 0), f"exit {r.returncode}")
    return finish()


# ---------------------------------------------------------------- #311 Russian voice
def cmd_speak_ru(a) -> int:
    with tempfile.TemporaryDirectory(prefix="tlwu-ru-") as td:
        wav = zh_wav(Path(td) / "zh.wav")
        res, out_dir = Path(td) / "r.json", Path(td) / "tts"
        md = ["--models-dir", a.models_dir] if a.models_dir else []
        # exactly what the issue asks for: --to ru and nothing else about the voice
        r = subprocess.run([PY, "-m", "tinglongwu.reverse.proto", "--input", str(wav), "--to", "ru", "--compute",
                            "cpu", "--mt", "hymt", "--no-play", "--no-vmic", "--no-confirm", "--out", str(out_dir),
                            "--save-result", str(res), *md, *whisper_args()], env=ENV, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1200)
        out = r.stdout + r.stderr
        print(tail(out, 40))
        check("speak --to ru starts without voice flags (#311 items 1-3)",
              r.returncode == 0 and "not installed" not in out.lower() and "WinError 87" not in out
              and "No module named 'vosk_tts'" not in out, f"exit {r.returncode}")
        items = json.loads(res.read_text(encoding="utf-8")).get("items", []) if res.exists() else []
        cyr = [it.get("ru", "") for it in items if re.search(r"[Ѐ-ӿ]", it.get("ru", ""))]
        check("Chinese speech translated to Russian", bool(cyr), repr(cyr[:1]))
        wavs = sorted(out_dir.glob("*.wav")) if out_dir.exists() else []
        spoken = 0.0
        if wavs:
            import soundfile as sf

            spoken = sum(sf.info(str(w)).duration for w in wavs)
        check("Russian voice synthesized (Vosk-TTS)", spoken > 1.0, f"{len(wavs)} wav, {spoken:.1f} s of speech")
    return finish()


# ---------------------------------------------------------------- #8 listen / speak / meeting on files (no block)
def cmd_modes(a) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import firewall_test as fw

    ns = argparse.Namespace(listen_wav=Path(a.listen_wav), speak_wav=Path(a.speak_wav), to="zh", mt=MT,  # #331: Hy-MT2 (opus was removed)
                            compute="cpu", tts_python=os.environ.get("TLWU_KOKORO_PYTHON", ""),
                            tts_model=os.environ.get("TLWU_KOKORO_MODEL", ""))
    with tempfile.TemporaryDirectory(prefix="tlwu-modes-") as td:
        for m in fw.plan(ns, Path(td)):
            ok, detail = fw.run_mode(m, fw.NoBlock(), a.timeout)
            check(f"{m.name} mode on audio files", ok, detail)
    return finish()


# ---------------------------------------------------------------- #192 / #186 speak mode on a PulseAudio monitor
def cmd_monitor_live(a) -> int:
    """PulseAudio null sink 'Monitor of <desc>' as the speak-mode microphone (the #192 crash), the Chinese clip
    played into the sink with paplay, Kokoro voice to the tlwu virtual mic; then Ctrl+C (#5, live path)."""
    sys.path.insert(0, str(ROOT))
    import soundcard as sc

    from tinglongwu.capture.mic import MicSource

    mon = f"Monitor of {a.sink_desc}"
    try:
        got = MicSource(device=mon)._pick_microphone(sc).name
    except Exception as e:  # noqa: BLE001
        got = f"{type(e).__name__}: {e}"
    check("MicSource resolves the PulseAudio monitor source (#192)", got.startswith("Monitor of"), got)
    with tempfile.TemporaryDirectory(prefix="tlwu-mon-") as td:
        wav = zh_wav(Path(td) / "zh.wav", repeat=a.repeat, gap_s=2.0)
        md = ["--models-dir", a.models_dir] if a.models_dir else []
        cmd = [PY, "-m", "tinglongwu.reverse.proto", "--mic", mon, "--allow-monitor", "--to", "en", "--compute",
               "cpu", "--mt", MT, "--no-confirm", "--debug-latency", *md, *whisper_args(), *kokoro_args()]
        p = subprocess.Popen(cmd, cwd=str(ROOT), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
        buf: list[str] = []
        listening = _wait_ready(p, re.compile(r"^listening"), 600, buf)
        check("speak mode opens 'Monitor of ...' and listens (#192)", listening, tail("\n".join(buf), 5))
        if listening:
            kids = _descendants(p.pid)
            subprocess.run(["paplay", f"--device={a.sink}", str(wav)], check=False, timeout=600)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 60 and sum("total" in ln and "[vad" in ln for ln in buf) < a.min_lines:
                time.sleep(0.5)
            time.sleep(3)
            os.killpg(p.pid, signal.SIGINT)
            second = False
            try:
                rc = p.wait(timeout=20)
            except subprocess.TimeoutExpired:  # still shutting down: the user presses Ctrl+C again (#5 design)
                second = True
                os.killpg(p.pid, signal.SIGINT)
                try:
                    rc = p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    p.kill()
                    _unload_vmic()
                    rc = None
            time.sleep(1)
            out = "\n".join(buf)
            timings = [ln.strip() for ln in buf if re.search(r"\[vad [\d.-]+ \| stt [\d.]+ \| mt [\d.]+ .*total", ln)]
            check("per-stage timing line per utterance (#186)", len(timings) >= a.min_lines,
                  f"{len(timings)} lines, e.g. {timings[:1]}")
            totals = sorted(float(re.search(r"total ([\d.]+)", t).group(1)) for t in timings)
            if totals:
                med = totals[len(totals) // 2]
                print(f"#186 runner CPU: median end-of-speech -> TTS start {med:.2f} s over {len(totals)} "
                      f"utterances (target 2.5 s is for the owner's CPU class; not gated here)")
                results.append(("#186 median total (info)", True, f"{med:.2f} s over {len(totals)} utterances"))
            ok = (rc in CLEAN_RC and not second and "Traceback" not in out and "Fatal Python error" not in out
                  and not _alive(kids))
            detail = (f"rc={rc}, needed a second Ctrl+C: {second}, children left: {len(_alive(kids))}, "
                      f"SIGABRT: {rc == -signal.SIGABRT}")
            # #427: gating (it was report-only while live speak mode hung / aborted after Ctrl+C on Linux)
            if not check("Ctrl+C in live speak mode on Linux (#5, #427)", ok, detail):
                print(tail(out, 30))
        else:
            p.kill()
            _unload_vmic()
            print(tail("\n".join(buf), 30))
    return finish()


# ---------------------------------------------------------------- #322 overlay above a fullscreen window
FS_APP = ("import tkinter as t; r=t.Tk(); r.title('tlwu-e2e-fullscreen'); r.configure(bg='black'); "
          "r.attributes('-fullscreen', True); r.after(60000, r.destroy); r.mainloop()")


class _X11:
    """X11 + a window manager (CI: Xvfb + openbox). Stacking = root's children order (bottom first)."""

    def __init__(self):
        from Xlib import display  # python-xlib (CI only)

        self.disp = display.Display()
        self.root = self.disp.screen().root

    def find(self, title: str):
        out = subprocess.run(["xdotool", "search", "--name", f"^{title}$"], capture_output=True, text=True).stdout
        return int(out.split()[0]) if out.split() else None

    def handle(self, tkwin) -> int:
        return int(tkwin.wm_frame(), 16)

    def activate(self, wid: int) -> None:  # the user clicks into the fullscreen browser
        subprocess.run(["xdotool", "windowactivate", "--sync", str(wid)], timeout=10, check=False)
        subprocess.run(["xdotool", "windowraise", str(wid)], timeout=10, check=False)

    def _index(self, wid: int) -> int:
        for i, k in enumerate(self.root.query_tree().children):
            stack = [k]
            while stack:
                w = stack.pop()
                if w.id == wid:
                    return i
                try:
                    stack += w.query_tree().children
                except Exception:  # noqa: BLE001
                    pass
        return -1

    def above(self, ov: int, fs: int) -> tuple[bool, str]:
        self.disp.sync()
        i_ov, i_fs = self._index(ov), self._index(fs)
        return i_ov > i_fs >= 0, f"stack index bar={i_ov} fullscreen={i_fs}"

    def visible(self, ov: int) -> tuple[bool, str]:
        g = self.disp.create_resource_object("window", ov).get_geometry()
        return g.width > 0 and g.height > 0, f"{g.width}x{g.height}"


class _Win32:
    """Windows desktop: EnumWindows lists top-level windows in Z order, topmost first."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.u = ctypes.windll.user32
        self.ct, self.wt = ctypes, wintypes

    def find(self, title: str):
        h = self.u.FindWindowW(None, title)
        return h or None

    def handle(self, tkwin) -> int:
        return int(tkwin.wm_frame(), 16)

    def activate(self, h: int) -> None:
        self.u.ShowWindow(h, 9)
        self.u.BringWindowToTop(h)
        self.u.SetForegroundWindow(h)

    def _order(self) -> list[int]:
        out: list[int] = []
        proto = self.ct.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)
        self.u.EnumWindows(proto(lambda h, _l: out.append(int(h or 0)) or True), 0)
        return out

    def above(self, ov: int, fs: int) -> tuple[bool, str]:
        order = self._order()
        i_ov = order.index(ov) if ov in order else -1
        i_fs = order.index(fs) if fs in order else -1
        ex = self.u.GetWindowLongW(ov, -20)  # GWL_EXSTYLE
        top = bool(ex & 0x8)  # WS_EX_TOPMOST
        return 0 <= i_ov < i_fs and top, f"z-order bar={i_ov} fullscreen={i_fs} (0 = top), WS_EX_TOPMOST={top}"

    def visible(self, ov: int) -> tuple[bool, str]:
        r = self.wt.RECT()
        self.u.GetWindowRect(ov, self.ct.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        return bool(self.u.IsWindowVisible(ov)) and w > 0 and h > 0, f"{w}x{h}"


def cmd_overlay_stack(a) -> int:
    import queue

    sys.path.insert(0, str(ROOT))
    from tinglongwu import overlay

    ws = _Win32() if WIN else _X11()
    fs = subprocess.Popen([PY, "-c", FS_APP])
    try:
        fs_id = None
        for _ in range(100):
            fs_id = ws.find("tlwu-e2e-fullscreen")
            if fs_id:
                break
            time.sleep(0.1)
        check("fullscreen window is up", bool(fs_id))
        if not fs_id:
            return finish()
        time.sleep(1.0)
        with tempfile.TemporaryDirectory(prefix="tlwu-ov-") as td:
            gj = Path(td) / "gui.json"
            bar = overlay.Bar(queue.Queue(), threading.Event(), gj)
            try:
                bar.root.update()
                ov = ws.handle(bar.overlay.win)
                times = []
                for i in range(6):  # line 0 warms up fonts / first layout; the median of the next 5 is checked
                    t0, want = time.monotonic(), f"你好 {i}"
                    bar.q.put(("final", f"hello {i}", want))
                    while want not in bar.overlay.text.get("1.0", "end") and time.monotonic() - t0 < 2:
                        bar.root.update()
                    times.append(time.monotonic() - t0)
                med = sorted(times[1:])[2]
                check("line on the bar within 100 ms (median of 5)", med < 0.1,
                      f"{med * 1000:.0f} ms (all: {', '.join(f'{t * 1000:.0f}' for t in times)} ms)")
                for rnd in range(3):  # the user clicks into the fullscreen browser: it is raised and focused
                    ws.activate(fs_id)
                    t1 = time.monotonic()
                    while time.monotonic() - t1 < 1.0:
                        bar.root.update()
                        time.sleep(0.03)
                    ok, detail = ws.above(ov, fs_id)
                    check(f"bar stays above the fullscreen window (round {rnd + 1})", ok, detail)
                ok, detail = ws.visible(ov)
                check("bar is mapped and visible", ok, detail)
                # drag position + font size persist across a restart (same gui.json)
                bar.overlay.set_font(36)
                bar.overlay.win.geometry("+40+50")
                bar.settings.update(x=40, y=50)
                t1 = time.monotonic()
                while time.monotonic() - t1 < 0.3:
                    bar.root.update()
                    time.sleep(0.03)
            finally:
                bar.stop.set()
                bar.root.destroy()
            bar2 = overlay.Bar(queue.Queue(), threading.Event(), gj)
            try:
                s = bar2.settings
                check("font size and position survive a restart", s.get("font_size") == 36 and
                      (s.get("x"), s.get("y")) == (40, 50), f"font {s.get('font_size')} at {s.get('x')},{s.get('y')}")
            finally:
                bar2.root.destroy()
    finally:
        fs.kill()
    return finish()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("zh-wav")
    p.add_argument("out")
    p.add_argument("--repeat", type=int, default=1)
    p = sub.add_parser("ctrlc")
    p.add_argument("--mode", choices=["listen", "speak"], required=True)
    p.add_argument("--models-dir")
    p.add_argument("--ready-timeout", type=float, default=600)
    p.add_argument("--exit-timeout", type=float, default=60)
    p.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--result", help=argparse.SUPPRESS)
    for name in ("gpu-old", "selftest", "speak-ru"):
        sub.add_parser(name).add_argument("--models-dir")
    p = sub.add_parser("doctor")
    p.add_argument("--to", default="ru")
    p.add_argument("--models-dir")
    p.add_argument("--expect-ok", nargs="*", default=["speech model", "translation model"])
    p = sub.add_parser("modes")
    p.add_argument("--listen-wav", required=True)
    p.add_argument("--speak-wav", required=True)
    p.add_argument("--timeout", type=float, default=900)
    p = sub.add_parser("monitor-live")
    p.add_argument("--sink", default="e2e_speaker")
    p.add_argument("--sink-desc", default="E2E-Speaker")
    p.add_argument("--models-dir")
    p.add_argument("--repeat", type=int, default=2)
    p.add_argument("--min-lines", type=int, default=3)
    sub.add_parser("overlay-stack")
    a = ap.parse_args(argv)
    return {"zh-wav": cmd_zh_wav, "ctrlc": cmd_ctrlc, "gpu-old": cmd_gpu_old, "selftest": cmd_selftest,
            "doctor": cmd_doctor, "speak-ru": cmd_speak_ru, "modes": cmd_modes, "monitor-live": cmd_monitor_live,
            "overlay-stack": cmd_overlay_stack}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
