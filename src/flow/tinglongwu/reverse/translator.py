"""Speak-mode translator construction (split from proto.py, #663): Hy-MT2 only, no fallback engine (#331)."""
from __future__ import annotations

import argparse
import sys


# CPU expectation printed at startup (#331, acceptance 4b): Hy-MT2-1.8B Q4_K_M on a 4-core CPU takes about
# 1.5 s per sentence (#227 menu text, #186 Linux CPU speak latency ~4 s end to end); minimum spec in #280.
HYMT_CPU_NOTE = ("Hy-MT2 runs on the CPU here: expect about 1-1.5 s of translation per sentence "
                 "(~4 s from end of speech to audio, #186); a CUDA GPU with 2 GB free VRAM is much faster.")


def choose_speak_mt(kind: str, device: str, mdir, free_mib=None) -> tuple[str, str, str]:
    """(engine, mt_device, reason) for speak mode. Hy-MT2 is the only engine (#331).

    Hy-MT2 runs on CUDA when the GPU has >= engines.HYMT_MIN_FREE_MIB free (#66), else on the CPU. A missing GGUF is
    a ModelNotFound with the `tlwu-setup --hymt` command (no silent fallback)."""
    from ..engines import HYMT_MIN_FREE_MIB, free_vram_mib
    from ..hymt import hymt_path
    from ..models import require

    require(hymt_path(mdir), "Hy-MT2 translation model (the only speak-mode engine)", "--hymt")
    if device != "cuda":
        return "hymt", "cpu", "CPU mode"
    free = (free_mib or free_vram_mib)()
    if free is None:
        return "hymt", "cpu", "could not read free VRAM: Hy-MT2 on the CPU"
    if free < HYMT_MIN_FREE_MIB:
        return "hymt", "cpu", f"{free} MiB VRAM free < {HYMT_MIN_FREE_MIB} MiB: Hy-MT2 on the CPU"
    return "hymt", "cuda", f"{free} MiB VRAM free >= {HYMT_MIN_FREE_MIB} MiB"


def _mt_kind(v: str) -> str:
    """--mt value: auto | hymt. opus / qwen were removed (#331, #283): argparse exits 2 with how to fix old launchers."""
    if v in ("auto", "hymt"):
        return v
    raise argparse.ArgumentTypeError(f"{v!r} was removed (#331): Hy-MT2 is the only engine; use --mt auto "
                                     "(re-run the installer to regenerate an old tlwu-talk launcher)")


class _UnsupportedPair(RuntimeError):
    """Hy-MT2 loaded, but a language of the pair is not one it translates."""


class _NoFallback:
    """Base translator under JaRouter: Hy-MT2 is the only engine (#331), so a pair it cannot take, or a Hy-MT2 that
    failed mid-session, is an error with the reason instead of a silent engine switch."""

    def __init__(self):
        self.router = None
        self.glossary = None

    def _why(self) -> str:
        err = getattr(self.router, "hy_error", "") or "language not supported by Hy-MT2"
        return f"Hy-MT2 cannot translate this ({err[:160]}); there is no fallback engine (#331)"

    def preload(self, src: str, tgt: str) -> None:
        if getattr(self.router, "hy", None) is not None:
            raise _UnsupportedPair(self._why())
        raise RuntimeError(self._why())

    def translate(self, text: str, src: str, tgt: str):
        raise RuntimeError(self._why())

    draft = translate


def _router_warn(m: str, warn) -> None:
    """JaRouter warnings under _NoFallback: the pivot wording is wrong here, so a mid-session Hy-MT2 failure becomes
    one "Hy-MT2 stopped (reason)" line (#451); other pivot warnings are dropped."""
    if "Hy-MT2 failed (" in m:
        warn(f"tlwu: Hy-MT2 stopped ({m.split('Hy-MT2 failed (', 1)[1].split('); ', 1)[0]}); no fallback engine (#331)")
    elif "pivot" not in m:
        warn(m)


def build_translator(kind: str, device: str, mdir, to: str = "ru", follow_langs=("en", "ru"),
                     src: str = "zh", free_mib=None, hy_factory=None, warn=None):
    """zh->ru|en|ja (or en->zh with src en, #134) translator plus the reverse pair for the back-translation shown
    while confirming. Hy-MT2 direct for every pair (#331: no pivot, no other engine). Not installed -> ModelNotFound
    with `tlwu-setup --hymt`; installed but not loadable (llama-cpp-python missing/broken) -> SystemExit, one line."""
    from ..hymt import JaRouter

    kind, mt_dev, why = choose_speak_mt(kind, device, mdir, free_mib)
    print(f"translation engine: {kind} on {mt_dev} ({why})", flush=True)
    warn = warn or (lambda m: print(m, file=sys.stderr, flush=True))
    if mt_dev == "cpu":
        warn(HYMT_CPU_NOTE)
    base = _NoFallback()
    # JaRouter's own "falls back to the ... pivot" warnings are wrong here: _NoFallback raises with the reason instead
    mt = JaRouter(base, mdir, "tw", mt_dev, all_pairs=True, warn=lambda m: _router_warn(m, warn),
                  **({"factory": hy_factory} if hy_factory else {}))
    base.router = mt
    targets = tuple(follow_langs) if to == "follow" else (to,)
    try:
        for t in targets:  # follow: every spoken language
            mt.preload(src, t)  # fail early if the model cannot load
            mt.preload(t, src)  # back-translation for the confirm step
    except Exception as e:  # noqa: BLE001 - incl. OSError from a --to ja preload; KeyboardInterrupt passes
        if isinstance(e, _UnsupportedPair):  # Hy-MT2 is fine, the language is not (e.g. --follow-langs): no install hint
            raise SystemExit(f"tlwu: {e}") from e
        from ..llamacheck import FIX_HINT, INDEX, LLAMA_VERSION

        wheel = f"{INDEX}/cu124" if mt_dev == "cuda" else f"{INDEX}/cpu"   # keep GPU support on a CUDA machine
        raise SystemExit(f"tlwu: {e}. Not installed: pip install llama-cpp-python=={LLAMA_VERSION} --only-binary=:all: "
                         f"--extra-index-url {wheel} && tlwu-setup --hymt. Installed but broken: {FIX_HINT}") from e
    mt.history, mt._pair = [], None  # same surface as the other translators for confirm.back_translate
    return mt
