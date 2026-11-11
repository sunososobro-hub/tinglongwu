import importlib.util
import struct
import zlib
from pathlib import Path

import numpy as np

from tinglongwu.speaker_embed import SpeakerLabeler

_spec = importlib.util.spec_from_file_location(
    "eval_diarization", Path(__file__).resolve().parents[1] / "scripts" / "eval_diarization.py")
ed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ed)


def seg(secs, tag):  # the "embedding" is read back from the first sample: tag 0 / 1 / 2
    return np.full(int(16000 * secs), float(tag), dtype=np.float32)


def fake_embed(s):
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]][int(s[0])]


def ticking():
    t = [0.0]

    def clock():
        t[0] += 0.01
        return t[0]
    return clock


def run(items):
    return ed.evaluate(items, SpeakerLabeler(fake_embed, clock=ticking()))


def test_best_mapping_ignores_cluster_numbering():
    # clusters get numbered in order of appearance; truth labels are arbitrary names
    items = [("bob", seg(2, 0)), ("amy", seg(2, 1)), ("bob", seg(2, 0)), ("amy", seg(2, 1))]
    r = run(items)
    assert r["accuracy"] == 100.0 and r["n"] == 4


def test_errors_counted_and_short_sentences_excluded():
    items = [("a", seg(2, 0)), ("b", seg(2, 1)), ("a", seg(2, 1)), ("b", seg(0.5, 0))]
    r = run(items)
    assert r["skipped"] == 1 and r["n"] == 3
    assert r["accuracy"] == 66.67  # x100, 2 decimals


def test_p50_seconds_and_report_format():
    r = run([("a", seg(2, 0)), ("b", seg(2, 1)), ("a", seg(2, 0))])
    assert r["p50_s"] == 0.01
    out = ed.format_report(r)
    assert "accuracy x100: 100.00" in out and "spk p50 s: 0.0100" in out


def test_png_written(tmp_path):
    p = tmp_path / "c.png"
    ed.write_png(p, {"accuracy": 90.0, "p50_s": 0.03, "n": 3, "skipped": 0})
    b = p.read_bytes()
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", b[16:24])
    assert (w, h) == (400, 120)
    i = b.index(b"IDAT")
    n = struct.unpack(">I", b[i - 4:i])[0]
    assert len(zlib.decompress(b[i + 4:i + 4 + n])) == h * (1 + 3 * w)


def test_load_items_resamples_and_cli_script_help(tmp_path):
    import soundfile as sf
    sf.write(tmp_path / "x.wav", np.zeros(8000, dtype=np.float32), 8000)
    (tmp_path / "l.csv").write_text("file,speaker\nx.wav,a\n", encoding="utf-8")
    items = ed.load_items(tmp_path, tmp_path / "l.csv")
    assert items[0][0] == "a" and len(items[0][1]) == 16000
