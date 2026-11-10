"""End-to-end self-test (#90): really load the models and translate one bundled sentence. Offline.

`tlwu --selftest [--model M] [--engine E] [--device D]` loads Whisper on the selected device, transcribes
the bundled few-second English clip, then translates the sentence with the selected engine. Every step prints
OK/FAIL with its time, the reason and what to do about it. A failing step is downgraded (GPU -> CPU,
Qwen -> OPUS) and tested again; the configuration that finally worked is written to the GUI settings and the
whole result is appended to install.log in the log directory (where "Report a problem" reads it).
Exit code 0 = the final configuration works, 1 = even the downgraded one fails.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import userdirs

CLIP = Path(__file__).resolve().parent / "data" / "selftest_en.wav"
CLIP_TEXT = "Hello, this is a test of the translation system. The weather is nice today."
CPU_WHISPER = ("small", "base", "tiny")  # models that are usable in real time without a GPU
LOG_NAME = "install.log"
DRIVER_ADVICE = ("update the NVIDIA graphics driver (it must support CUDA 12; the newest driver from "
                 "nvidia.com is fine), then run the self-test again. Using the CPU tier for now")


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str = ""
    advice: str = ""

    def line(self) -> str:
        s = f"[{'OK' if self.ok else 'FAIL'}] {self.name} ({self.seconds:.1f} s)"
        if self.detail:
            s += f": {self.detail}"
        if self.advice:
            s += f"\n       -> {self.advice}"
        return s


@dataclass
class Result:
    steps: list[Step] = field(default_factory=list)
    device: str = "cpu"
    model: str = "small"
    engine: str = "opus"
    downgraded: bool = False  # anything stepped down (GPU -> CPU, speech model, engine)
    engine_downgraded: bool = False
    ok: bool = False

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.steps)

    def summary(self) -> str:
        head = "self-test PASSED" if self.ok else "self-test FAILED"
        if self.ok and self.downgraded:
            head += " after downgrade"
        return (f"{head}: device={self.device} speech model={self.model} translation={self.engine} "
                f"(total {self.seconds:.1f} s)")

    def text(self) -> str:
        return "\n".join([s.line() for s in self.steps] + [self.summary()])


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


def _default_asr(mdir, model, device):
    from .asr import FasterWhisperRecognizer

    return FasterWhisperRecognizer(mdir, model, "en", device)


def _default_mt(mdir, engine, zh_variant, device):
    """Build + load exactly the requested engine (no silent fallback: the self-test does the downgrade)."""
    if engine in ("qwen3-8b", "qwen3-4b"):
        from .qwen import QwenTranslator

        tr = QwenTranslator(mdir, engine, zh_variant, device)
    elif engine == "m2m":
        from .translate import M2mTranslator

        tr = M2mTranslator(mdir, zh_variant, device)
    else:
        from .translate import PivotTranslator

        tr = PivotTranslator(mdir, zh_variant, device=device)
    tr.preload("en", "zh")
    return tr


def _default_choose(engine, device, mdir):
    from .qwen import choose_engine

    return choose_engine(engine, device, mdir)[0]


def _default_has_engine(mdir, name):
    from .qwen import model_path

    return model_path(Path(mdir), name).exists()


def _default_resolve(choice, warn):
    from .device import resolve_device

    return resolve_device(choice, warn=warn)


def _default_gpu_name():
    from .hwprobe import gpu_info

    return gpu_info()[0]


def _installed(mdir: Path, model: str) -> bool:
    return (Path(mdir) / f"whisper-{model}").exists()


def cpu_model(mdir: Path, model: str) -> str:
    """Whisper model for the CPU tier: keep a small one, else the largest installed small one, else unchanged."""
    if model in CPU_WHISPER:
        return model
    for m in CPU_WHISPER:
        if _installed(mdir, m):
            return m
    return model


def _advice(e: BaseException) -> str:
    from .models import ModelNotFound

    if isinstance(e, ModelNotFound):
        return "the model is not downloaded: run the tlwu-setup command shown above once (with network)"
    if isinstance(e, ImportError):
        return "a package is missing or broken: run the installer again (tlwu-setup --deps)"
    return ""


def run_selftest(mdir, model: str = "small", engine: str = "auto", device: str = "auto",
                 zh_variant: str = "tw", *, make_asr=_default_asr, make_mt=_default_mt,
                 choose=_default_choose, resolve=_default_resolve, gpu_name=_default_gpu_name,
                 has_engine=_default_has_engine, clip=load_clip, clock=time.perf_counter) -> Result:
    mdir = Path(mdir)
    res = Result(model=model)
    steps = res.steps

    # ---- device: what the installer selected vs. what the runtime can really use
    t0 = clock()
    warned: list[str] = []
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
    if dev == "cpu":
        res.model = model = cpu_model(mdir, model)

    # ---- speech: load Whisper and transcribe the bundled clip; GPU failure -> CPU and test again
    try:
        audio = clip()
    except Exception as e:  # noqa: BLE001
        steps.append(Step("test clip", False, 0.0, _short(e), "the installation is incomplete: install again"))
        res.device = dev
        return res
    heard = None
    while True:
        t0 = clock()
        label = f"speech model {model} on {'GPU' if dev == 'cuda' else 'CPU'}"
        try:
            asr = make_asr(mdir, model, dev)
            used = getattr(asr, "device", dev)
            tr = asr.transcribe(audio)
            heard = (tr.text if tr else "").strip()
            if not heard:
                raise RuntimeError("the model returned no text for the test clip")
            if used != dev:  # the recognizer itself fell back while loading
                steps.append(Step(f"speech model {model} on GPU", False, 0.0,
                                  "could not be loaded on the GPU", DRIVER_ADVICE))
                res.downgraded, dev = True, used
                label = f"speech model {model} on CPU"
            steps.append(Step(label, True, clock() - t0, f"heard: {heard}"))
            break
        except Exception as e:  # noqa: BLE001 - driver / cuDNN / OOM / missing model: report, downgrade
            if dev == "cuda":
                steps.append(Step(label, False, clock() - t0, _short(e), DRIVER_ADVICE))
                res.downgraded, dev = True, "cpu"
                res.model = model = cpu_model(mdir, model)
                continue
            steps.append(Step(label, False, clock() - t0, _short(e), _advice(e)))
            res.device = dev
            return res
    res.device = dev

    # ---- translation: selected engine; Qwen failure -> OPUS and test again
    try:
        eng = choose(engine, dev, mdir)
    except Exception:  # noqa: BLE001
        eng = "opus"
    while True:
        t0 = clock()
        label = f"translation {eng} on {'GPU' if dev == 'cuda' else 'CPU'}"
        try:
            out = (make_mt(mdir, eng, zh_variant, dev).translate(CLIP_TEXT, "en", "zh").target or "").strip()
            if not _has_cjk(out):
                raise RuntimeError(f"no Chinese in the output: {out[:80]!r}")
            steps.append(Step(label, True, clock() - t0, out))
            res.engine, res.ok = eng, True
            return res
        except Exception as e:  # noqa: BLE001
            nxt = {"qwen3-8b": "qwen3-4b", "qwen3-4b": "opus"}.get(eng)
            if nxt == "qwen3-4b" and not has_engine(mdir, nxt):
                nxt = "opus"
            if nxt:
                steps.append(Step(label, False, clock() - t0, _short(e),
                                  f"using {nxt} instead (lower quality with OPUS). " + (_advice(e) or
                                  "If this is a GPU machine, update the NVIDIA driver and run the self-test again")))
                res.downgraded = res.engine_downgraded = True
                eng = nxt
                continue
            steps.append(Step(label, False, clock() - t0, _short(e), _advice(e)))
            res.engine = eng
            return res


GUI_ENGINE = {"qwen3-8b": "qwen", "qwen3-4b": "qwen4b", "opus": "opus"}


def apply_to_gui(res: Result, requested_model: str = "small", requested_engine: str = "auto", path=None) -> bool:
    """Write the configuration that passed into the GUI settings. Only what the self-test had to change is
    overwritten (a downgraded speech model / engine; CPU tier: live draft off); on a fresh install the tested
    model and engine become the defaults."""
    from .gui import core

    fresh = not Path(path or core.settings_path()).exists()
    s = core.load_settings(path)
    if fresh or res.model != requested_model:
        s["model"] = res.model
    if res.engine_downgraded and res.engine in GUI_ENGINE:
        s["engine"] = GUI_ENGINE[res.engine]
    elif fresh and requested_engine in core.ENGINES:
        s["engine"] = requested_engine
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
    res = run_selftest(models_dir(a.models_dir), a.model, a.engine, a.compute, a.zh_variant)
    print(res.text(), file=out, flush=True)
    log = write_log(res)
    if log:
        print(f"result saved to {log}", file=out)
    if res.ok:
        apply_to_gui(res, a.model, a.engine)
    return 0 if res.ok else 1
