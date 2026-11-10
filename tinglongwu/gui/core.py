"""GUI logic without any tkinter: event queue sink, subtitle state, settings persistence.

Worker threads only ever put tuples on a queue.Queue; the tkinter main thread polls it with after().
Events: ("draft", src_text, translation) | ("final", src_text, target_text) | ("clear",) |
        ("status", text) | ("notice", text) | ("error", text) | ("stopped",)
"""
from __future__ import annotations

import json
import os
import queue
from pathlib import Path

from ..sink import Result, Sink
from ..stream import DraftRenderer

DEFAULTS = {
    "lang": "auto", "target": "zh-tw", "engine": "auto", "model": "small", "glossary": "",
    "save": False, "save_path": "", "show_src": False, "font_size": 28, "live_draft": True,
    "x": None, "y": None, "source": "system", "preset": "meeting", "theme": "dark",
}
LANGS = ["auto", "ru", "ja", "en", "ko", "zh", "de", "fr", "es", "it", "pt", "vi", "th", "id"]
TARGETS = ["zh-tw", "zh-cn", "en"]
ENGINES = ["auto", "qwen", "qwen4b", "opus"]
PRESETS = ["meeting", "chat"]  # meeting = whole sentences (~5-8 s latency); chat = lower latency, more fragments
SOURCES = ["system", "mic"]  # system = whatever the PC is playing (loopback); mic = microphone
MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
FONT_MIN, FONT_MAX = 10, 96


def target_for_locale(tag: str | None) -> str:
    """OS locale tag -> default 'Translate to' value: zh-CN/SG -> zh-cn, other zh (and unknown/empty) -> zh-tw, else en."""
    t = (tag or "").strip().lower().replace("_", "-").split(".")[0]
    if not t or t in ("c", "posix"):
        return DEFAULTS["target"]
    if t.startswith("zh"):
        return "zh-cn" if ("hans" in t or t.endswith(("-cn", "-sg"))) and "hant" not in t else "zh-tw"
    return "en"


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "tinglongwu" / "gui.json"


def _gpu_usable() -> bool:
    from ..device import cuda_device_count

    return cuda_device_count() > 0


def load_settings(path=None, gpu=_gpu_usable) -> dict:
    """Saved settings merged over DEFAULTS; wrong-typed or unreadable values fall back to the default.
    First run (no saved file) without a usable GPU: live draft starts off, it doubles the work on a CPU."""
    out = dict(DEFAULTS)
    fresh = True
    try:
        from ..i18n import _system_tag

        out["target"] = target_for_locale(_system_tag())  # first run (no saved choice): follow the OS locale
    except Exception:  # noqa: BLE001
        pass
    try:
        data = json.loads(Path(path or settings_path()).read_text("utf-8"))
        if isinstance(data, dict):
            fresh = False
            for k, dv in DEFAULTS.items():
                v = data.get(k)
                if v is None:
                    continue
                if dv is None:  # x / y
                    if isinstance(v, int) and not isinstance(v, bool):
                        out[k] = v
                elif isinstance(v, type(dv)) and not (type(dv) is int and isinstance(v, bool)):
                    out[k] = v
    except Exception:  # noqa: BLE001 - missing/corrupt file: just use defaults
        pass
    out["font_size"] = clamp_font(out["font_size"])
    if fresh:
        try:
            out["live_draft"] = bool(gpu())
        except Exception:  # noqa: BLE001
            pass
    return out


def save_settings(s: dict, path=None) -> bool:
    try:
        p = Path(path or settings_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({k: s.get(k, DEFAULTS[k]) for k in DEFAULTS}, ensure_ascii=False, indent=2), "utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


def clamp_font(n) -> int:
    try:
        return max(FONT_MIN, min(FONT_MAX, int(n)))
    except (TypeError, ValueError):
        return DEFAULTS["font_size"]


def settings_to_argv(s: dict) -> list[str]:
    """Map GUI settings to CLI args (so the GUI reuses the CLI's parser and defaults)."""
    to, variant = {"zh-tw": ("zh", "tw"), "zh-cn": ("zh", "cn"), "en": ("en", "tw")}.get(s.get("target"), ("zh", "tw"))
    argv = ["--lang", s.get("lang") or "auto", "--to", to, "--zh-variant", variant,
            "--engine", s.get("engine") or "auto", "--model", s.get("model") or "small",
            "--preset", s.get("preset") if s.get("preset") in PRESETS else "meeting"]
    if s.get("source") == "mic":
        argv += ["--mic"]
    if s.get("glossary"):
        argv += ["--glossary", s["glossary"]]
    if s.get("save"):
        argv += ["--save"] + ([s["save_path"]] if s.get("save_path") else [])
    return argv


class QueueSink(Sink):
    """Final lines -> queue (runs on the worker thread; never touches tkinter)."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, r: Result) -> None:
        self.q.put(("final", r.original, r.target if r.src != r.tgt and r.target else r.original))


class QueueDraftRenderer(DraftRenderer):
    """Drop-in for DraftRenderer that publishes draft events instead of writing ANSI to a terminal."""

    def __init__(self, q: queue.Queue):
        super().__init__(stream=None, columns=lambda: 100, enabled=True)
        self.q = q

    def clear(self) -> None:
        self.q.put(("clear",))

    def draw(self, src_tag, confirmed, tail, tgt_tag="", translation="", show_src=True) -> None:
        src = (confirmed + (" " + tail if confirmed and tail and " " in (confirmed + tail) else tail)).strip()
        if translation:
            self.q.put(("draft", src, translation))
        elif show_src:  # same-language / transcript-only: the source itself is the draft
            self.q.put(("draft", "", src))


class SubtitleState:
    """What the overlay shows: last `max_lines` lines; a draft is replaced in place by the final."""

    def __init__(self, max_lines: int = 2):
        self.max_lines = max_lines
        self.finals: list[tuple[str, str]] = []  # (src, target), oldest first
        self.draft: tuple[str, str] | None = None
        self.paused = False  # frozen display: events still update the state (nothing is lost) but nothing is redrawn

    def apply(self, ev: tuple) -> bool:
        """Apply an event; True if the display should be redrawn."""
        if self._apply(ev):
            return not self.paused
        return False

    def _apply(self, ev: tuple) -> bool:
        kind = ev[0]
        if kind == "draft":
            self.draft = (ev[1], ev[2])
        elif kind == "final":
            self.draft = None
            self.finals.append((ev[1], ev[2]))
            del self.finals[:-self.max_lines]
        elif kind == "clear":
            if self.draft is None:
                return False
            self.draft = None
        else:
            return False
        return True

    def lines(self) -> list[tuple[str, bool]]:
        """[(target_text, is_draft)] oldest first, at most max_lines."""
        rows = [(t, False) for _, t in self.finals]
        if self.draft:
            rows.append((self.draft[1], True))
        return rows[-self.max_lines:]

    def source(self) -> str:
        """Source text of the newest shown line."""
        if self.draft:
            return self.draft[0]
        return self.finals[-1][0] if self.finals else ""

    def last_text(self) -> str:
        rows = self.lines()
        return rows[-1][0] if rows else ""
