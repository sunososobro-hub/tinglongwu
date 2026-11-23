"""#1357 (#1342): start listening while the Hy-MT2 model is still downloading.

A PendingTranslator stands in for the MT engine. Until the model file is complete, `mt_ready()` is False and
`pipeline.run` writes source-language lines (the transcript-only line shape). It checks the model at most every
`poll_s` seconds from the MT worker; once the file is complete it builds the real engine in a background thread
(loading takes seconds and must never stall ASR or the output), and every later line is translated. A line goes out
either as source or translated, never both, so nothing is lost or written twice at the hand-over. A failed load is
reported once and the session stays source-only. No network here: the download is someone else's job (the GUI's
tlwu-setup child, #1358); this only watches the file.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .translate import Translation

POLL_S = 2.0  # seconds between model-file checks; a line waits for nothing, so this only bounds the hand-over delay


def hymt_complete(mdir, size: str = "1.8b") -> bool:
    """True when the Hy-MT2 GGUF is on disk AND models/manifest.json pins it. The downloader writes `<file>.part`
    and renames it only after the SHA256 check, and writes the manifest after that, so both together mean done."""
    from .hymt import HYMT_SIZES, hymt_path

    if not hymt_path(mdir, size).is_file():
        return False
    mf = Path(mdir) / "manifest.json"
    try:
        man = json.loads(mf.read_text("utf-8")) if mf.is_file() else {}
    except (OSError, ValueError):
        return False
    return HYMT_SIZES[size][0] in man


class PendingTranslator:
    """MT engine placeholder: source-only until `complete()` is True, then the engine `build()` returns.

    complete() -> bool: the model is fully on disk (cheap; called at most every poll_s seconds).
    build() -> translator: loads the engine (slow; runs in its own thread). Raising (or SystemExit, which
    cli.make_translator uses for a load failure) keeps the session source-only and calls warn(msg) once.
    `preload` and `glossary` set before the engine exists are applied to it when it is built."""

    def __init__(self, complete, build, zh_variant: str = "tw", poll_s: float = POLL_S, warn=None,
                 clock=time.monotonic, on_ready=None):
        self._complete, self._build, self._clock = complete, build, clock
        self.zh_variant = zh_variant
        self.poll_s = poll_s
        self._warn = warn or (lambda m: None)
        self._on_ready = on_ready
        self._lock = threading.Lock()
        self._inner = None
        self._thread: threading.Thread | None = None
        self._next = 0.0
        self.error: str | None = None
        self._preload: list[tuple[str, str]] = []
        self._glossary = None
        self._has_glossary = False

    # -- state -------------------------------------------------------------------------------------------------
    def mt_ready(self) -> bool:
        """True once the real engine is loaded. While not, starts the load when the model file has landed."""
        if self._inner is not None:
            return True
        self._maybe_start()
        return self._inner is not None

    def notify(self) -> None:
        """Check the model file now (e.g. the download child just exited) instead of at the next poll."""
        with self._lock:
            self._next = 0.0
        self._maybe_start()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for a running load (tests, shutdown). True when the engine is ready."""
        t = self._thread
        if t is not None:
            t.join(timeout)
        return self._inner is not None

    def _maybe_start(self) -> None:
        with self._lock:
            if self._inner is not None or self.error is not None or self._thread is not None:
                return
            now = self._clock()
            if now < self._next:
                return
            self._next = now + self.poll_s
            if not self._complete():
                return
            t = threading.Thread(target=self._load, name="tlwu-mt-load", daemon=True)
            t.start()
            self._thread = t  # set only once started: wait() may join it from another thread at any time

    def _load(self) -> None:
        try:
            tr = self._build()
            if self._has_glossary and hasattr(tr, "glossary"):
                tr.glossary = self._glossary
            for src, tgt in self._preload:
                tr.preload(src, tgt)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - any load failure: stay source-only, say it once
            self.error = str(e)[:300] or type(e).__name__
            self._warn(f"tlwu: translation engine could not start, showing source text only: {self.error}")
            return
        with self._lock:
            self._inner = tr
        self._warn("tlwu: translation model ready, translating from the next line")
        if self._on_ready is not None:
            self._on_ready()

    # -- Translator surface ------------------------------------------------------------------------------------
    @property
    def glossary(self):
        return self._inner.glossary if self._inner is not None and hasattr(self._inner, "glossary") \
            else self._glossary

    @glossary.setter
    def glossary(self, value) -> None:
        self._glossary, self._has_glossary = value, True
        if self._inner is not None and hasattr(self._inner, "glossary"):
            self._inner.glossary = value

    def preload(self, src: str, tgt: str) -> None:
        if self._inner is not None:
            self._inner.preload(src, tgt)
        else:
            self._preload.append((src, tgt))

    def translate(self, text: str, src: str, tgt: str, context: str | None = None) -> Translation:
        if self._inner is None:
            return Translation(None, "")
        if context:  # #1750: only reached when takes_context() saw the loaded engine take it
            return self._inner.translate(text, src, tgt, context=context)
        return self._inner.translate(text, src, tgt)

    def draft(self, text: str, src: str, tgt: str) -> Translation:
        if self._inner is None:
            return Translation(None, "")
        return self._inner.draft(text, src, tgt)

    def __getattr__(self, name):  # other engine attributes (step times, base, ...) once loaded
        inner = self.__dict__.get("_inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)


def live(translator):
    """The engine to translate a line with now: the translator itself, or None while a PendingTranslator waits."""
    ready = getattr(translator, "mt_ready", None)
    if ready is None:
        return translator
    return translator if ready() else None
