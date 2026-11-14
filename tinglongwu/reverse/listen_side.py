"""Listen side inside the speak process (#285; split from proto.py, #663)."""
from __future__ import annotations

import os
import sys
import threading

from .synth import describe_error


class ListenSide:
    """The `tlwu` listen pipeline in a thread of this process (#285). Its final lines go to the same console
    (◀ Them) as the speak side's (▶ Me); close() stops it. Errors on the listen side are reported and leave
    the speak side running."""

    def __init__(self, argv, run=None, parse=None, bar_q=None):
        from .. import cli

        bad = [x for x in argv if x in ("--stream", "--gui", "--report", "--probe", "--overlay")]
        if bad:
            hint = " (put --overlay before --listen: one bar shows both sides)" if bad[0] == "--overlay" else ""
            raise ValueError(f"--listen: {bad[0]} is not available next to the speak side{hint}")
        self.bar_q = bar_q
        self.args = (parse or cli.parse_args)(list(argv))
        self._run = run or cli.run_session
        self.saver = None
        if self.args.save is not None:  # #373: one transcript for both sides; opened here so ▶ lines can go in too
            from ..sink import JsonlSaveSink, default_save_path

            path = self.args.save or default_save_path()
            self.saver = JsonlSaveSink(None, path)
            self.args.save = None  # run_session must not wrap the sink a second time
            print(f"tlwu: saving final lines (both sides) to {os.path.abspath(path)}", file=sys.stderr)
        self.notes = None
        if self.saver is not None and getattr(self.args, "topics", None):  # #609: notes over both sides
            from .. import topics

            self.notes = topics.attach(self.saver, self.args.topics)
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._body, name="tlwu-listen", daemon=True)

    def _body(self) -> None:
        try:
            if self.bar_q is None and self.saver is None:
                self._run(self.args, stop=self.stop)
            else:
                from ..sink import TextSink, TranscriptSink

                sink = TranscriptSink() if self.args.transcript_only else TextSink()
                if self.bar_q is not None:  # #322: the other side's lines also go to the shared bar, marked ◀
                    from ..overlay import OverlaySink

                    sink = OverlaySink(sink, self.bar_q, prefix="◀ ")
                if self.saver is not None:  # #373: heard lines -> side="them" in the shared transcript
                    self.saver.inner = sink
                    sink = self.saver
                self._run(self.args, stop=self.stop, sink=sink)
        except BaseException as e:  # noqa: BLE001 - SystemExit from a missing model too: report, keep speaking
            self.error = e
            if not self.stop.is_set():
                print(f"tlwu: listen side stopped: {describe_error(e)}", file=sys.stderr, flush=True)

    def start(self) -> "ListenSide":
        self.thread.start()
        return self

    def close(self, timeout: float = 5.0) -> None:
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout)
        if self.notes is not None:  # #609: meeting over, finish the last topic and the backlog
            self.notes.close()
            self.notes = None
