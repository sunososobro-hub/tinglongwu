"""#609 (part 2 of #595): offline per-topic meeting notes.

Final transcript lines (both sides, from the --save writer) are grouped into topics; each closed topic is
summarized by a local GGUF instruct model in ONE background worker thread at the lowest thread priority, so the
ASR/MT threads only pay for a list append and a queue.put. If the worker falls behind, topics wait in the queue and
are finished after the meeting (close() drains it). Notes go to `<transcript>.topics.md` next to the --save file.

Off unless `--topics MODEL.gguf` is given (needs --save). The model is picked by the #595 part 3 bake-off; the
GUI side panel and defaults are part 4. Topics are cut by the #608 TopicSegmenter (pause rules; embeddings opt-in).
"""
from __future__ import annotations

import os
import queue
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .topic_segmenter import MAX_S, MIN_S, PAUSE_S, TopicSegmenter

MAX_BULLETS = 5
SUMMARY_TOKENS = 220  # #595: ~120 output tokens of notes, with headroom for the labels


@dataclass
class Line:
    t: float
    side: str  # "me" / "them"
    text: str


@dataclass
class Topic:
    title: str
    bullets: list[str] = field(default_factory=list)
    actions: list[tuple[str, str]] = field(default_factory=list)  # (who = me/them, action)
    start: float = 0.0
    end: float = 0.0


def PauseSegmenter(pause_s: float = PAUSE_S, min_s: float = MIN_S, max_s: float = MAX_S) -> TopicSegmenter:
    """Pause/min/max rules only (no embedding): the #608 segmenter without an `embed` function."""
    return TopicSegmenter(pause_s, min_s, max_s)


def build_prompt(lines: list[Line]) -> str:
    body = "\n".join(f"[{ln.side}] {ln.text}" for ln in lines)
    return ("Summarize this part of a meeting transcript. [me] is the user, [them] is the other side.\n"
            "Write in the same language as the transcript, in exactly this format:\n"
            "TITLE: <short topic title>\n- <key point>\n(3 to 5 key points)\n"
            "ACTION me: <what the user must do>\nACTION them: <what the other side must do>\n"
            "(only actions that were really agreed; none is fine)\n\nTranscript:\n" + body)


_ACTION = re.compile(r"^\s*(?:[-*]\s*)?ACTION\s*\(?\s*(me|them)\s*\)?\s*[:：]\s*(.+)$", re.I)
_TITLE = re.compile(r"^\s*TITLE\s*[:：]\s*(.+)$", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$")


def parse_summary(text: str, start: float = 0.0, end: float = 0.0) -> Topic:
    """Model output -> Topic; tolerant of extra blank lines, numbering and missing parts."""
    title, bullets, actions = "", [], []
    for raw in (text or "").splitlines():
        if m := _ACTION.match(raw):
            actions.append((m.group(1).lower(), m.group(2).strip()))
        elif m := _TITLE.match(raw):
            title = title or m.group(1).strip()
        elif m := _BULLET.match(raw):
            bullets.append(m.group(1).strip())
    if not title:
        title = bullets.pop(0) if bullets else "Topic"
    return Topic(title, bullets[:MAX_BULLETS], actions, start, end)


def _mmss(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60:02d}:{s % 60:02d}"


def render_markdown(topics: list[Topic]) -> str:
    out = ["# Meeting topics", ""]
    for i, tp in enumerate(topics, 1):
        out.append(f"## {i}. {tp.title} ({_mmss(tp.start)}-{_mmss(tp.end)})")
        out += [f"- {b}" for b in tp.bullets]
        if tp.actions:
            out += ["", "Action items:"] + [f"- [ ] ({who}) {what}" for who, what in tp.actions]
        out.append("")
    return "\n".join(out)


def lower_thread_priority() -> bool:
    """Lowest scheduling priority for the CALLING thread (Linux: nice 19 on the thread id; Windows:
    THREAD_PRIORITY_IDLE). False where it is not supported (macOS) or refused."""
    try:
        if sys.platform == "win32":
            import ctypes

            k32 = ctypes.windll.kernel32
            return bool(k32.SetThreadPriority(k32.GetCurrentThread(), -15))  # THREAD_PRIORITY_IDLE
        if sys.platform.startswith("linux"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)  # Linux: per-thread nice
            return True
    except (OSError, AttributeError):
        pass
    return False


class TopicSummarizer:
    """One daemon worker: summarize(prompt) -> text for each submitted topic, in order, at the lowest priority.
    submit() never waits for the model; close() lets the queue drain (the after-the-meeting backlog)."""

    def __init__(self, summarize: Callable[[str], str], on_topic: Callable[[Topic], None],
                 lower_priority: Callable[[], bool] = lower_thread_priority, log=None):
        self.summarize, self.on_topic, self._lower = summarize, on_topic, lower_priority
        self.log = log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.q: queue.Queue = queue.Queue()
        self.lowered: bool | None = None
        self.thread = threading.Thread(target=self._body, name="tlwu-topics", daemon=True)
        self.thread.start()

    def submit(self, lines: list[Line]) -> None:
        if lines:
            self.q.put(list(lines))

    def pending(self) -> int:
        return self.q.qsize()

    def _body(self) -> None:
        self.lowered = self._lower()
        while True:
            lines = self.q.get()
            if lines is None:
                return
            try:
                text = self.summarize(build_prompt(lines))
                self.on_topic(parse_summary(text, lines[0].t, lines[-1].t))
            except Exception as e:  # noqa: BLE001 - notes are a side feature: never stop the meeting over them
                self.log(f"tlwu: topic summary failed: {e}")

    def close(self, timeout: float | None = None) -> None:
        self.q.put(None)
        self.thread.join(timeout)


class TopicNotes:
    """Feeds saved transcript records (JsonlSaveSink.on_line) into a segmenter and the summarizer; rewrites the
    Markdown file after every finished topic. on_line runs on the ASR/MT threads: it only appends and enqueues."""

    def __init__(self, path, summarize: Callable[[str], str], segmenter=None,
                 lower_priority: Callable[[], bool] = lower_thread_priority, log=None):
        self.path = Path(path)
        self.segmenter = segmenter or PauseSegmenter()
        self.topics: list[Topic] = []
        self._lock = threading.Lock()  # both sides call on_line (listen thread + speak thread)
        self.worker = TopicSummarizer(summarize, self._done, lower_priority=lower_priority, log=log)

    def on_line(self, rec: dict) -> None:
        text = (rec.get("dst") or rec.get("src") or "").strip()  # the translation (user's language) when there is one
        if not text or "fix" in rec:
            return
        with self._lock:
            closed = self.segmenter.add(Line(float(rec.get("t", 0.0)), rec.get("side", "them"), text))
        for seg in closed:
            self.worker.submit(seg)

    def _done(self, topic: Topic) -> None:  # worker thread
        self.topics.append(topic)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(render_markdown(self.topics), encoding="utf-8")

    def close(self, timeout: float | None = None) -> None:
        """Meeting over: summarize the open topic too and wait for the backlog."""
        with self._lock:
            rest = self.segmenter.flush()
        for seg in rest:
            self.worker.submit(seg)
        left = self.worker.pending()
        if left:
            print(f"tlwu: finishing {left} topic summar{'y' if left == 1 else 'ies'} ...", file=sys.stderr, flush=True)
        self.worker.close(timeout)


def notes_path(save_path) -> Path:
    p = Path(save_path)
    return p.with_name(p.stem + ".topics.md")


def llama_summarizer(model_path, threads: int | None = None, loader=None) -> Callable[[str], str]:
    """A summarize(prompt) on a local GGUF via llama.cpp, loaded lazily on the worker thread (CPU, few threads so
    ASR/MT keep their cores). Separate from the translation model: a different file."""
    state: dict = {}
    n = threads or max(1, (os.cpu_count() or 4) // 4)

    def _load():
        if loader is not None:
            return loader(model_path)
        from . import llamacheck

        llamacheck.import_llama()
        from llama_cpp import Llama

        return Llama(model_path=str(model_path), n_gpu_layers=0, n_ctx=4096, n_threads=n, n_threads_batch=n,
                     verbose=False)

    def summarize(prompt: str) -> str:
        if "llm" not in state:
            state["llm"] = _load()
        r = state["llm"].create_chat_completion([{"role": "user", "content": prompt}], max_tokens=SUMMARY_TOKENS,
                                                temperature=0.0)
        return r["choices"][0]["message"]["content"] or ""

    return summarize


def attach(saver, model_path, summarize: Callable[[str], str] | None = None) -> TopicNotes:
    """Hook topic notes onto a JsonlSaveSink (both sides in meeting mode go through its record())."""
    notes = TopicNotes(notes_path(saver.path), summarize or llama_summarizer(model_path))
    saver.on_line = notes.on_line
    print(f"tlwu: topic notes -> {os.path.abspath(notes.path)}", file=sys.stderr)
    return notes
