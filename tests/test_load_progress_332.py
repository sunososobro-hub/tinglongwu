"""#332: every loading stage is announced with elapsed seconds, then a ready summary (CLI and GUI callback)."""
import io
import sys
import types

from tinglongwu import cli


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_load_progress_stages_in_order_with_elapsed_and_summary():
    clk, said = Clock(), []
    p = cli.LoadProgress(said.append, clock=clk)
    clk.t += 0.5
    p.stage("speech model")
    clk.t += 3.0
    p.stage("translation engine")
    clk.t += 8.25
    summary = p.finish()
    assert said == ["loading speech model... (0.5 s)", "loading translation engine... (3.5 s)"]
    assert summary == "loaded in 11.8 s (speech model 3.0 s, translation engine 8.2 s)"


def _run_session(monkeypatch, tmp_path, status=None, argv=()):
    """run_session with slow fake loaders on a fake clock: the speech model takes 4 s, translation 9 s."""
    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod

    clk, events = Clock(), []
    monkeypatch.setattr(cli, "time", types.SimpleNamespace(monotonic=clk))
    report = status.append if status is not None else None

    class Asr:
        device, live = "cpu", False

        def __init__(self, *a, **k):
            events.append("asr loaded")
            clk.t += 4.0

    class Mt:
        def preload(self, src, tgt):
            clk.t += 9.0

    def make_translator(*a, **k):
        events.append("mt loaded")
        return Mt()

    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", Asr)
    monkeypatch.setattr(cli, "make_translator", make_translator)
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: object())
    monkeypatch.setattr(pipeline_mod, "run", lambda *a, **k: events.append("pipeline run"))
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    args = cli.parse_args(["--lang", "ru", "--file", str(tmp_path / "in.wav"), "--model", "tiny",
                           "--models-dir", str(tmp_path), *argv])
    if report is not None:
        status.clear()

        def report(m):  # the GUI status callback also sees the order relative to the loaders
            status.append(m)
            events.append(m)
    cli.run_session(args, status=report)
    return events, err.getvalue()


def test_gui_status_callback_fires_once_per_stage_in_order_then_ready(monkeypatch, tmp_path):
    seen = []
    events, _ = _run_session(monkeypatch, tmp_path, status=seen)
    assert seen == ["loading speech model... (0.0 s)",
                    "loading translation engine... (4.0 s)",
                    "listening - loaded in 13.0 s (speech model 4.0 s, translation engine 9.0 s)"]
    # each note comes before its loader runs, and ready only after everything loaded, before the pipeline
    assert events == [seen[0], "asr loaded", seen[1], "mt loaded", seen[2], "pipeline run"]
    assert seen[-1].startswith(cli.READY_STATUS)


def test_cli_prints_stage_lines_and_summary_to_stderr(monkeypatch, tmp_path, capsys):
    _, err = _run_session(monkeypatch, tmp_path)
    lines = [ln for ln in err.splitlines() if ln.startswith(("tlwu: loading", "tlwu: loaded", "* ready"))]
    assert lines == ["tlwu: loading speech model... (0.0 s)",
                     "tlwu: loading translation engine... (4.0 s)",
                     "tlwu: loaded in 13.0 s (speech model 4.0 s, translation engine 9.0 s)",
                     cli.READY_LINE]
    assert capsys.readouterr().out == ""  # stdout keeps only transcript/translation lines (#357)


def test_transcript_only_has_no_translation_stage(monkeypatch, tmp_path):
    seen = []
    _run_session(monkeypatch, tmp_path, status=seen, argv=["--transcript-only"])
    assert seen == ["loading speech model... (0.0 s)", "listening - loaded in 4.0 s (speech model 4.0 s)"]
