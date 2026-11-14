"""End-to-end self-test (#90): really load the models and translate one bundled sentence. Offline.

`tlwu --selftest [--model M] [--engine E] [--device D]` loads Whisper on the selected device, transcribes the
bundled few-second English clip (`data/selftest_en.wav`), then translates the sentence with the selected engine.
Every step prints OK/FAIL with its time, the reason and what to do about it. A GPU failure is downgraded to the
CPU tier and tested again (Hy-MT2 stays the engine: #276, never a silent engine switch). The result is appended
to install.log in the log directory ("Report a problem" attaches it) and the configuration that passed is written
to the GUI settings. Exit code 0 = the final configuration works, 1 = it does not.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import userdirs

CLIP = Path(__file__).resolve().parent / "data" / "selftest_en.wav"
CLIP_TEXT = "Hello, this is a test of the translation system. The weather is nice today."
CPU_WHISPER = ("small", "base", "tiny")  # usable in real time without a GPU
LOG_NAME = "install.log"
DRIVER_ADVICE = ("update the NVIDIA graphics driver (it must support CUDA 12; the newest driver from nvidia.com "
                 "is fine), then run the self-test again. Using the CPU tier for now")


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str = ""
    advice: str = ""

    def line(self) -> str:
        s = f"[{'OK' if self.ok else 'FAIL'}] {self.name} ({self.seconds:.1f} s)"
        s += f": {self.detail}" if self.detail else ""
        return s + (f"\n       -> {self.advice}" if self.advice else "")


@dataclass
class Result:
    steps: list[Step] = field(default_factory=list)
    device: str = "cpu"
    model: str = "small"
    engine: str = "hymt2"
    downgraded: bool = False
    ok: bool = False
    mt_backend: str = ""  # "cuda" | "vulkan" | "cpu" (#746)

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.steps)

    def text(self) -> str:
        head = ("self-test PASSED" + (" after downgrade" if self.downgraded else "")) if self.ok else "self-test FAILED"
        return "\n".join([s.line() for s in self.steps] + [
            f"{head}: device={self.device} speech model={self.model} translation={self.engine} "
            + (f"mt: {self.mt_backend} " if self.mt_backend else "") + f"(total {self.seconds:.1f} s)"])


def load_clip(path: Path = CLIP):
    """The bundled clip as float32 mono 16 kHz."""
    import soundfile as sf

    audio, sr = sf.read(str(path), dtype="float32")
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        raise RuntimeError(f"{path.name}: expected 16 kHz audio, got {sr} Hz")
    return audio


def _short(e: BaseException, n: int = 300) -> str:
    return " ".join(f"{type(e).__name__}: {e}".split())[:n]


def _has_cjk(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def _silent(*_a, **_k) -> None:
    pass


def _default_asr(mdir, model, device):
    from .asr import FasterWhisperRecognizer

    return FasterWhisperRecognizer(mdir, model, "en", device)


def _default_mt(mdir, name, zh_variant, device, ollama_model=None, ollama_url=None):
    """Build exactly engine `name` (hymt2 / hymt2-cpu / ollama / fm) the way a session does; failures raise."""
    from .cli import make_translator

    return make_translator(name, mdir, zh_variant, device, say=_silent, choose=lambda *_a: (name, "self-test"),
                           ollama_model=ollama_model, ollama_url=ollama_url)


def _default_choose(engine, device, mdir):
    from .engines import choose_engine

    return choose_engine(engine, device, mdir)[0]


def _default_resolve(choice, warn):
    from .device import resolve_device

    return resolve_device(choice, warn=warn)


def _default_gpu_name():
    from .hwprobe import gpu_info

    return gpu_info()[0]


def _default_pick(mdir, device):
    from .cli import auto_model

    return auto_model("auto", device, mdir, say=_silent)


def cpu_model(mdir: Path, model: str) -> str:
    """Whisper model for the CPU tier: keep a small one, else the largest small one installed, else unchanged."""
    if model in CPU_WHISPER:
        return model
    return next((m for m in CPU_WHISPER if (Path(mdir) / f"whisper-{m}").exists()), model)


def _advice(e: BaseException) -> str:
    from .models import ModelNotFound

    if isinstance(e, ModelNotFound):
        return "the model is not downloaded: run the tlwu-setup command shown above once (with network)"
    if isinstance(e, ImportError):
        return "a package is missing or broken: run the installer again"
    return ""


def _where(dev: str) -> str:
    return "GPU" if dev == "cuda" else "CPU"


def run_selftest(mdir, model: str | None = None, engine: str = "auto", device: str = "auto", zh_variant: str = "tw",
                 *, make_asr=_default_asr, make_mt=_default_mt, choose=_default_choose, resolve=_default_resolve,
                 gpu_name=_default_gpu_name, pick=_default_pick, clip=load_clip, clock=time.perf_counter) -> Result:
    mdir = Path(mdir)
    res = Result()
    steps = res.steps

    # ---- device: what was selected vs. what the runtime can really use
    t0, warned = clock(), []
    try:
        dev = resolve(device, warned.append)
    except Exception as e:  # noqa: BLE001
        warned.append(_short(e))
        dev = "cpu"
    name = None
    if device != "cpu":
        try:
            name = gpu_name()
        except Exception:  # noqa: BLE001
            name = None
    if dev == "cuda":
        steps.append(Step("GPU", True, clock() - t0, name or "CUDA device usable"))
    elif name:  # nvidia-smi sees a card but the runtime cannot use it (#89: driver too old)
        why = warned[0] if warned else "CTranslate2 reports no usable CUDA device although nvidia-smi sees the card"
        steps.append(Step("GPU", False, clock() - t0, f"{name}: {why}", DRIVER_ADVICE))
        res.downgraded = True
    else:
        steps.append(Step("device", True, clock() - t0, "CPU (no NVIDIA GPU requested or found)"))

    def model_for(d: str) -> str:
        if model is None:
            try:
                return pick(mdir, d)
            except Exception:  # noqa: BLE001
                return "small"
        return cpu_model(mdir, model) if d == "cpu" else model

    res.model = cur = model_for(dev)

    # ---- speech: load Whisper and transcribe the clip; a GPU failure -> CPU tier and test again
    try:
        audio = clip()
    except Exception as e:  # noqa: BLE001
        steps.append(Step("test clip", False, 0.0, _short(e), "the installation is incomplete: install again"))
        res.device = dev
        return res
    while True:
        t0 = clock()
        label = f"speech model {cur} on {_where(dev)}"
        try:
            asr = make_asr(mdir, cur, dev)
            used = getattr(asr, "device", dev)
            tr = asr.transcribe(audio)
            heard = (getattr(tr, "text", "") if tr else "").strip()
            if not heard:
                raise RuntimeError("the model returned no text for the test clip")
            if used != dev:  # the recognizer itself fell back to the CPU while loading
                steps.append(Step(label, False, 0.0, "could not be loaded on the GPU", DRIVER_ADVICE))
                res.downgraded, dev = True, used
                label = f"speech model {cur} on {_where(dev)}"
            steps.append(Step(label, True, clock() - t0, f"heard: {heard}"))
            break
        except Exception as e:  # noqa: BLE001 - driver / cuDNN / OOM / missing model: report, downgrade
            if dev == "cuda":
                steps.append(Step(label, False, clock() - t0, _short(e), DRIVER_ADVICE))
                res.downgraded, dev = True, "cpu"
                res.model = cur = model_for(dev)
                continue
            steps.append(Step(label, False, clock() - t0, _short(e), _advice(e)))
            res.device = dev
            return res
    res.device = dev

    # ---- translation: the selected engine; Hy-MT2 failing on the GPU -> Hy-MT2 on the CPU (same engine, #276)
    try:
        eng = choose(engine, dev, mdir)
    except Exception:  # noqa: BLE001
        eng = "hymt2-cpu" if dev != "cuda" else "hymt2"
    while True:
        t0 = clock()
        on = "cpu" if eng == "hymt2-cpu" else dev
        res.engine = "hymt2" if eng.startswith("hymt2") else eng
        label = f"translation {res.engine} on {_where(on)}"
        try:
            from . import mt_backend

            mt_backend.last_backend = ""  # reported only if THIS translator's load set it (no stale value)
            out = (make_mt(mdir, eng, zh_variant, dev).translate(CLIP_TEXT, "en", "zh").text or "").strip()
            if not _has_cjk(out):
                raise RuntimeError(f"no Chinese in the output: {out[:80]!r}")
            steps.append(Step(label, True, clock() - t0, out))
            res.mt_backend = mt_backend.last_backend or ("cuda" if on == "cuda" else "cpu")
            res.ok = True
            return res
        except (Exception, SystemExit) as e:  # noqa: BLE001 - make_translator stops with SystemExit + advice
            if eng == "hymt2" and on == "cuda":
                steps.append(Step(label, False, clock() - t0, _short(e),
                                  "running Hy-MT2 on the CPU instead (slower). If this is a GPU machine, update the "
                                  "NVIDIA driver or close other GPU programs, then run the self-test again"))
                res.downgraded, eng = True, "hymt2-cpu"
                continue
            steps.append(Step(label, False, clock() - t0, _short(e), _advice(e) if isinstance(e, Exception) else ""))
            return res


def apply_to_gui(res: Result, requested_model: str | None = None, path=None) -> bool:
    """Write the configuration that passed into the GUI settings: the tested speech model on a fresh install or
    when it had to change, and live draft off on the CPU tier. The engine is left alone (auto already runs Hy-MT2
    on the CPU when the GPU cannot, #276)."""
    from .gui import core

    fresh = not Path(path or core.settings_path()).exists()
    s = core.load_settings(path)
    if fresh or res.model != (requested_model or s.get("model")):
        s["model"] = res.model
    if res.device == "cpu" and (fresh or res.downgraded):
        s["live_draft"] = False
    return core.save_settings(s, path)


def write_log(res: Result, path=None) -> Path | None:
    try:
        p = Path(path) if path else userdirs.log_dir() / LOG_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"--- tlwu self-test {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{res.text()}\n")
        return p
    except OSError:
        return None


def main_from_args(a, out=None) -> int:
    """`tlwu --selftest`: a = parsed CLI args."""
    from .models import models_dir

    out = out or sys.stdout
    print("tlwu self-test: loading the models and translating one test sentence (offline)...", file=out, flush=True)
    def make_mt(mdir, name, zh_variant, device):  # #356: --ollama-model / --ollama-url apply to the self-test too
        return _default_mt(mdir, name, zh_variant, device, getattr(a, "ollama_model", None),
                           getattr(a, "ollama_url", None))

    res = run_selftest(models_dir(a.models_dir), a.model, a.engine, a.compute, a.zh_variant, make_mt=make_mt)
    print(res.text(), file=out, flush=True)
    log = write_log(res)
    if log:
        print(f"result saved to {log}", file=out)
    if res.ok:
        try:
            apply_to_gui(res, a.model)
        except Exception as e:  # noqa: BLE001 - no tkinter etc.: the self-test result still stands
            print(f"(GUI settings not updated: {_short(e, 120)})", file=out)
    return 0 if res.ok else 1
