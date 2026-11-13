"""#222: --speakers wiring. Embeddings are mocked; sherpa-onnx is never needed."""
import io
import re
import subprocess
import sys

import numpy as np

from tinglongwu import cli, fmt
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.capture import FileSource
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink
from tinglongwu.speaker_embed import SpeakerLabeler
from pivot_fake import PivotTranslator


class FakeASR(Recognizer):
    def transcribe(self, audio):
        return Transcript("привет мир", "ru")


def steps(s, t):
    return {("ru", "en"): lambda x: "hello world", ("en", "zh"): lambda x: "你好世界"}[(s, t)]


def _run(wav, tmp_path, capsys, speakers=None, debug=False):
    out = io.StringIO()
    log = (lambda m: print(m, file=sys.stderr)) if debug else None
    run(FileSource(str(wav)), Segmenter(chunk=1.0, use_vad=False), FakeASR(),
        PivotTranslator(tmp_path, "tw", steps), TextSink(out), speakers=speakers, log=log)
    return out.getvalue(), capsys.readouterr().err


def test_flag_default_off():
    assert cli.parse_args(["--file", "x.wav"]).speakers is False
    assert cli.parse_args(["--file", "x.wav", "--speakers"]).speakers is True


def test_off_output_and_log_unchanged(wav, tmp_path, capsys):
    out, err = _run(wav, tmp_path, capsys, debug=True)
    assert "說話者" not in out + err
    assert "spk" not in err
    assert "latency: asr" in err


def test_on_shows_speaker_on_the_line_and_spk_column(wav, tmp_path, capsys):
    off_out, _ = _run(wav, tmp_path, capsys)
    ticks = iter(range(1000))
    lab = SpeakerLabeler(lambda seg: [1.0, 0.0], clock=lambda: next(ticks) * 0.01)
    out, err = _run(wav, tmp_path, capsys, speakers=lab, debug=True)
    strip = lambda t: re.sub(r"\d\d:\d\d:\d\d", "", t)  # noqa: E731
    # #384: the speaker replaces the generic "them" label on the same line; translations do not change
    assert strip(out) == strip(off_out).replace(fmt.t("label_them"), "◀ 說話者1")
    assert "說話者1" in out
    assert "tlwu: 說話者1" not in err  # no separate console line, the debug log keeps it
    assert "latency: spk 0.010 s (說話者1)" in err


def test_labeler_two_speakers_and_short_sentence():
    vecs = iter([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
    lab = SpeakerLabeler(lambda seg: next(vecs))
    seg = np.zeros(16000 * 2, np.float32)
    assert [lab.label(seg)[0] for _ in range(3)] == ["說話者1", "說話者2", "說話者1"]
    called = []
    lab2 = SpeakerLabeler(lambda seg: called.append(1) or [1.0, 0.0])
    assert lab2.label(np.zeros(1600, np.float32))[0] == "?"  # < 1 s: model not called
    assert called == []


def test_off_never_imports_sherpa():
    code = ("import sys; from tinglongwu import cli, pipeline, speaker_embed; "
            "cli.parse_args(['--file','x.wav']); "
            "sys.exit(1 if 'sherpa_onnx' in sys.modules else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
