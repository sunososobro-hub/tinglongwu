"""#1358: download the missing default listen models in the background, one `tlwu-setup --default-listen` child per
missing key per app run. No Tk here: the GUI feeds `on_status` / `on_ready` and shows the text it gets.

The child (resumable, SHA256-checked via tinglongwu_setup.resume) prints `download <key>: N%` lines; the network code
stays in that child, this module only starts it, parses the lines and reports the result."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

# keep in sync with tinglongwu_setup.defaults.DEFAULT_LISTEN_KEYS (the runtime package must not import the setup
# package; tests/test_gui_bg_download_1342.py compares the two)
DEFAULT_KEYS = ("whisper-small", "hymt2-1.8b")
LABEL = {"whisper-small": "speech model", "hymt2-1.8b": "translation"}
_PCT = re.compile(r"download\s+(\S+?):\s*(\d{1,3})\s*%")


def missing_keys(mdir, keys=DEFAULT_KEYS) -> list[str]:
    """Keys that are not installed: pinned in manifest.json and their dir exists (same rule as pack_on_demand)."""
    mf = Path(mdir) / "manifest.json"
    man = json.loads(mf.read_text("utf-8")) if mf.exists() else {}
    return [k for k in keys if not (k in man and (Path(mdir) / k).exists())]


def parse_percent(line: str) -> tuple[str, int] | None:
    """`download hymt2-1.8b: 35% (387/1100 MB)` -> ("hymt2-1.8b", 35); other lines -> None."""
    m = _PCT.search(line or "")
    return (m.group(1), min(100, int(m.group(2)))) if m else None


def _tqdm_hit(line: str, keys) -> tuple[str, int] | None:
    """huggingface_hub (snapshot_download, the Whisper model) prints tqdm bars, not `download <key>:` lines; they
    count for the Whisper key while it is among the keys this child fetches."""
    from .core import progress_bar

    bar = progress_bar(line)
    key = DEFAULT_KEYS[0]
    return (key, bar[1]) if bar and key in keys else None


def status_text(key: str, percent: int | None) -> str:
    name = LABEL.get(key, key)
    return f"{name} downloading {percent}%" if percent is not None else f"{name} downloading"


def error_text(key: str, err: str) -> str:
    """Error line + the retry button text (the GUI shows the second part on the retry button)."""
    return f"{LABEL.get(key, key)} download failed: {err}".strip() + " - press Start to retry"


class BackgroundDownloads:
    """Starts at most one child per missing key per run (a key already covered by a running child gets no second one).

    start(popen=...) -> list of keys a new child was started for. `popen(cmd)` returns an object with `.stdout`
    (iterable of lines) and `.wait()`; `cmd` is the child command."""

    def __init__(self, cmd, on_status, on_ready, mdir, popen=None, threaded: bool = True):
        self.cmd, self.on_status, self.on_ready, self.mdir = cmd, on_status, on_ready, mdir
        self.popen, self.threaded = popen, threaded
        self.active: set[str] = set()  # keys with a running child
        self.started: dict[str, int] = {}  # key -> children started this run
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self) -> list[str]:
        with self._lock:
            todo = [k for k in missing_keys(self.mdir) if k not in self.active]
            if not todo:
                return []
            self.active.update(todo)
            for k in todo:
                self.started[k] = self.started.get(k, 0) + 1
        if self.threaded:
            threading.Thread(target=self._run, args=(todo,), daemon=True).start()
        else:
            self._run(todo)
        return todo

    def _run(self, keys: list[str]) -> None:
        import subprocess

        from .core import download_env  # same env as the foreground children: offline flags removed

        popen = self.popen or (lambda c: subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                          text=True, env=download_env()))
        last = ""
        try:
            p = popen(self.cmd)
            for raw in p.stdout:
                line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip()
                if line:
                    last = line
                hit = parse_percent(line) or _tqdm_hit(line, keys)
                if hit and hit[0] in keys:
                    self.on_status(status_text(*hit))
            rc = p.wait()
        except Exception as e:  # noqa: BLE001
            rc, last = -1, f"{type(e).__name__}: {e}"[:90]
        with self._lock:
            self.active.difference_update(keys)
        if rc == 0:
            for k in keys:
                self.errors.pop(k, None)
                self.on_ready(k)
        else:
            for k in keys:
                self.errors[k] = last[:90] or f"exit {rc}"
                self.on_status(error_text(k, self.errors[k]))
