"""Extra `tlwu` arguments for the listen side of the helper menus (#42), shared by linux_menu.sh and
windows_menu.ps1 so both behave the same:

    python -m tinglongwu.menuopts listen    -> prints one argument per line on stdout (notes/questions on stderr)

* Glossary: if the user glossary folder (<config dir>/glossary, e.g. %APPDATA%\\tinglongwu\\glossary or
  ~/.config/tinglongwu/glossary) holds *.txt files, add `--glossary <folder>`. Putting files there is the opt-in.
* CPU translation engine: without a usable GPU `--engine auto` uses OPUS (fast, but the ru->en->zh pivot can lose or
  flip meaning, e.g. negations) unless `tlwu --probe --speed` already chose Hy-MT2. When Hy-MT2 is installed and no
  remembered choice exists, ask once per start (default = keep auto); `h` adds `--engine hymt`.
Offline: reads local files only; never downloads or probes speed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable


def glossary_dir() -> Path:
    from .userdirs import config_dir

    return config_dir() / "glossary"


def _glossary_args(say) -> list[str]:
    from .i18n import t

    d = glossary_dir()
    files = sorted(d.glob("*.txt")) if d.is_dir() else []
    if not files:
        return []
    say(t("menu_glossary_on", n=len(files), dir=d))
    return ["--glossary", str(d)]


def _hymt_ready(mdir) -> bool:
    from .hymt import hymt_path

    if not hymt_path(mdir).is_file():
        return False
    try:
        import llama_cpp  # noqa: F401 - same check as the menus' have_llama
    except Exception:  # noqa: BLE001 - missing or broken build: Hy-MT2 cannot run here
        return False
    return True


def _engine_args(ask, say, device, mdir, cpu_choice, hymt_ready) -> list[str]:
    from .i18n import t

    if device() != "cpu" or not hymt_ready(mdir) or cpu_choice() is not None:
        return []  # GPU: auto already prefers Hy-MT2; cached CPU choice: auto already uses it
    if os.environ.get("TLWU_MT", "").lower() == "opus":
        return []
    say(t("menu_cpu_mt_hymt"))  # owner 2026-11-11 (#227): Hy-MT2 by default on CPU, no question
    return ["--engine", "hymt"]


def listen_args(ask: Callable[[str], str] | None = None, say: Callable[[str], None] | None = None,
                device=None, mdir=None, cpu_choice=None, hymt_ready=None) -> list[str]:
    """Arguments the menu appends to its `tlwu` listen command. Every dependency is injectable for tests."""
    say = say or (lambda m: print(m, file=sys.stderr, flush=True))
    if ask is None:
        def ask(prompt: str) -> str:
            sys.stderr.write(prompt)
            sys.stderr.flush()
            line = sys.stdin.readline()
            if not line:
                raise EOFError
            return line
    if device is None:
        from .device import resolve_device

        def device():
            return resolve_device("auto", warn=lambda m: None)
    if mdir is None:
        from .models import models_dir

        mdir = models_dir(None)
    if cpu_choice is None:
        from .speedprobe import cpu_choice
    out = _glossary_args(say)
    try:
        out += _engine_args(ask, say, device, mdir, cpu_choice, hymt_ready or _hymt_ready)
    except Exception as e:  # noqa: BLE001 - a broken probe must never stop the menu: keep --engine auto
        say(f"tlwu: engine check skipped ({str(e)[:120]})")
    return out


def speak_mt(to: str, ask=None, say=None, hymt_ready=None, mdir=None) -> str:
    """Translation engine for the speak side (#227): `hymt` whenever Hy-MT2 is installed (no question; env TLWU_MT=opus
    overrides), else `opus` plus a one-line install hint. `to`/`ask` are kept for callers."""
    from .i18n import t

    say = say or (lambda m: print(m, file=sys.stderr, flush=True))
    if ask is None:
        def ask(prompt: str) -> str:
            sys.stderr.write(prompt)
            sys.stderr.flush()
            line = sys.stdin.readline()
            if not line:
                raise EOFError
            return line
    if mdir is None:
        from .models import models_dir

        mdir = models_dir(None)
    try:
        ready = (hymt_ready or _hymt_ready)(mdir)
    except Exception:  # noqa: BLE001
        ready = False
    if not ready:
        say(t("menu_speak_mt_hint"))
        return "opus"
    return "opus" if os.environ.get("TLWU_MT", "").lower() == "opus" else "hymt"  # owner 2026-11-11: no question


def stream_default(device: str, both: bool) -> bool:
    """#257 / #273: default answer of the live-draft (`--stream`) question. On in listen-only mode on any device
    (re-translation: drafts show within ~2 s, finals are whole sentences); the listen+speak mode (background listener
    must not redraw the terminal) stays off."""
    return not both


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "stream-default":  # arg: listen | both ; prints 1 (default on) or 0
        from .device import resolve_device

        try:
            dev = resolve_device("auto", warn=lambda m: None)
        except Exception:  # noqa: BLE001 - unknown device: keep the old default (off)
            dev = "unknown"
        print("1" if stream_default(dev, argv[1] == "both") else "0")
        return 0
    if len(argv) == 2 and argv[0] == "speak":
        print(speak_mt(argv[1]))
        return 0
    if argv != ["listen"]:
        print("usage: python -m tinglongwu.menuopts listen | speak ru|en | stream-default listen|both", file=sys.stderr)
        return 2
    for x in listen_args():
        print(x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
