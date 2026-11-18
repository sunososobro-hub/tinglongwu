"""#1256: the annotated LibriSpeech conversation builder and the label-count gate of scripts/eval_diarization.py."""
import importlib.util
import io
import random
import sys
import tarfile
from pathlib import Path

import numpy as np
import pytest

from tinglongwu.speaker_embed import SpeakerLabeler
from tinglongwu.speakers import SpeakerClusterer

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dc = _load("diarization_clip", "scripts/bench/diarization_clip.py")
ed = _load("eval_diarization_1256", "scripts/eval_diarization.py")
SR = 16000


def flac(seconds, value=0.1):
    import soundfile as sf

    b = io.BytesIO()
    sf.write(b, np.full(int(SR * seconds), value, dtype=np.float32), SR, format="FLAC")
    return b.getvalue()


def tar_gz(members):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return b.getvalue()


def libri(spk, n, chapter=100):
    out = [(f"LibriSpeech/test-clean/{spk}/{chapter}/{spk}-{chapter}.trans.txt", b"x")]
    out += [(f"LibriSpeech/test-clean/{spk}/{chapter}/{spk}-{chapter}-{i:04d}.flac", flac(0.2)) for i in range(n)]
    return out


class Reader:
    """A fake HTTP response that records how many bytes were read."""

    def __init__(self, data):
        self.buf, self.read_bytes = io.BytesIO(data), 0

    def read(self, n=-1):
        d = self.buf.read(n)
        self.read_bytes += len(d)
        return d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_speaker_of_reads_librispeech_paths():
    assert dc.speaker_of("LibriSpeech/test-clean/1089/134686/1089-134686-0000.flac") == "1089"
    assert dc.speaker_of("LibriSpeech/test-clean/1089/134686/1089-134686.trans.txt") is None
    assert dc.speaker_of("LibriSpeech/SPEAKERS.TXT") is None


def test_stream_takes_first_speakers_and_stops_early():
    data = tar_gz([("LibriSpeech/SPEAKERS.TXT", b"x")] + libri("11", 5) + libri("22", 5) + libri("33", 5)
                  + [("LibriSpeech/test-clean/44/1/44-1-0000.flac", np.random.default_rng(0).bytes(400_000))])
    got = dc.stream_speakers(io.BytesIO(data), n_speakers=2, per_speaker=3)
    assert sorted(got) == ["11", "22"] and all(len(v) == 3 for v in got.values())
    r = Reader(data)
    got = dc.fetch_speakers(3, per_speaker=5, fetch=lambda url: r, urls=("u",))
    assert sorted(got) == ["11", "22", "33"]
    assert r.read_bytes < len(data) - 100_000  # speaker 44 (the big tail) was never downloaded


def test_fetch_falls_back_to_next_mirror_and_caps_download():
    data = tar_gz(libri("11", 2))

    def fetch(url):
        if url == "bad":
            raise OSError("mirror down")
        return Reader(data)
    assert list(dc.fetch_speakers(1, per_speaker=2, fetch=fetch, urls=("bad", "good"))) == ["11"]
    r = Reader(data)
    with pytest.raises(dc.DownloadError):
        dc.fetch_speakers(1, per_speaker=2, fetch=lambda u: r, urls=("good",), max_bytes=100)
    assert r.read_bytes == 100  # nothing is read past the cap


def test_fetch_tries_next_mirror_on_short_or_broken_body():
    import http.client

    full = tar_gz(libri("11", 2) + libri("22", 2))
    short = tar_gz(libri("11", 2))

    class Broken(Reader):
        def read(self, n=-1):
            if self.read_bytes:
                raise http.client.IncompleteRead(b"")
            return super().read(min(n, 50))
    seen = []

    def fetch(url):
        seen.append(url)
        return {"short": Reader(short), "broken": Broken(full), "good": Reader(full)}[url]
    got = dc.fetch_speakers(2, per_speaker=2, fetch=fetch, urls=("short", "broken", "good"))
    assert sorted(got) == ["11", "22"] and seen == ["short", "broken", "good"]
    with pytest.raises(dc.DownloadError):
        dc.fetch_speakers(2, per_speaker=2, fetch=lambda u: Reader(short), urls=("short",))


def test_cut_covers_audio_with_piece_bands():
    rng = random.Random(1)
    n = int(SR * 300)
    pieces = dc.cut(n, rng)
    assert pieces[0][0] == 0 and pieces[-1][1] == n
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))  # consecutive, nothing lost
    secs = [(e - b) / SR for b, e in pieces]
    assert min(secs) >= dc.MIN_PIECE and max(secs) < 8.0 + dc.MIN_PIECE
    short = sum(1 for s in secs if 1.0 <= s < 2.0) / len(secs)
    assert 0.2 < short < 0.5  # about a third are the short 1-2 s sentences of #1256


def test_conversation_turns_alternate_and_are_repeatable():
    utts = {s: [np.zeros(SR * 10, dtype=np.float32)] for s in ("a", "b", "c")}
    conv = dc.conversation(utts, random.Random(7), turns_per_speaker=4)
    assert {s for s, _ in conv} == {"a", "b", "c"}
    assert [s for s, _ in conv] == [s for s, _ in dc.conversation(utts, random.Random(7), turns_per_speaker=4)]
    # a turn has at most 3 pieces, so one speaker never holds the floor for more than 3 pieces in a row
    run, longest = 1, 1
    for x, y in zip(conv, conv[1:]):
        run = run + 1 if x[0] == y[0] else 1
        longest = max(longest, run)
    assert longest <= 3


def test_write_clip_feeds_eval_load_items(tmp_path):
    items = [("a", np.zeros(SR * 2, dtype=np.float32)), ("b", np.zeros(SR, dtype=np.float32))]
    labels = dc.write_clip(tmp_path, items)
    got = ed.load_items(tmp_path, labels)
    assert [s for s, _ in got] == ["a", "b"] and len(got[0][1]) == SR * 2


def seg(secs, tag):
    return np.full(int(SR * secs), float(tag), dtype=np.float32)


def embed(s):  # the "embedding" is chosen by the first sample
    return {0: [1.0, 0.0, 0.0], 1: [0.0, 1.0, 0.0], 2: [0.6, 0.0, 0.8], 3: [0.0, 0.0, 1.0]}[int(s[0])]


def test_label_count_counts_merged_labels_once():
    # speaker "a" first sounds like vector 0, then drifts (tag 2: cosine 0.6 to vector 0, 0 to vector 1 -> opens a 3rd label at
    # threshold 0.65); a merge of label 3 into label 1 must count as one label the user sees
    clus = SpeakerClusterer(threshold=0.65, merge_threshold=0.95)
    lab = SpeakerLabeler(embed, clus)
    r = ed.evaluate([("a", seg(5, 0)), ("b", seg(5, 1)), ("a", seg(5, 2))], lab)
    assert (r["speakers"], r["labels_raw"], r["labels"]) == (2, 3, 3)
    clus.merged["說話者3"] = "說話者1"  # what _merge_near records when it joins the two
    r2 = ed.evaluate([], lab)
    assert r2["labels"] == 0
    seen = ed.evaluate([("a", seg(5, 0)), ("a", seg(5, 2))], lab)
    assert seen["labels"] == 1 and seen["labels_raw"] == 2 and seen["accuracy"] == 100.0


def run_cli(tmp_path, monkeypatch, tags, ratio):
    import soundfile as sf
    import tinglongwu.speaker_embed as se

    rows = ["file,speaker"]
    for i, (spk, tag) in enumerate(tags):
        sf.write(tmp_path / f"{i}.wav", seg(3, tag), SR, subtype="FLOAT")  # keep the tag value
        rows.append(f"{i}.wav,{spk}")
    (tmp_path / "l.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(se, "make_labeler", lambda mdir: SpeakerLabeler(embed))
    return ed.main([str(tmp_path), str(tmp_path / "l.csv"), "--png", str(tmp_path / "c.png"),
                    "--max-label-ratio", str(ratio)])


def test_cli_fails_when_labels_exceed_ratio(tmp_path, monkeypatch, capsys):
    # two real speakers, but "a" also appears as an orthogonal voice (tag 3): 3 labels / 2 speakers = 1.5x
    tags = [("a", 0), ("b", 1), ("a", 3), ("b", 1)]
    assert run_cli(tmp_path, monkeypatch, tags, 1.4) == 1
    assert "FAIL: 3 labels for 2 speakers (1.50x > 1.4x" in capsys.readouterr().out
    assert run_cli(tmp_path, monkeypatch, tags, 1.5) == 0
    out = capsys.readouterr().out
    assert "PASS: 3 labels for 2 speakers" in out and "speakers: 2  labels: 3" in out


def test_cli_fails_when_no_label_is_produced(tmp_path, monkeypatch, capsys):
    import tinglongwu.speaker_embed as se

    class Unknown:
        def label(self, seg):
            return "?", 0.0
    run_cli(tmp_path, monkeypatch, [("a", 0)], 0)
    monkeypatch.setattr(se, "make_labeler", lambda mdir: Unknown())
    assert ed.main([str(tmp_path), str(tmp_path / "l.csv"), "--png", str(tmp_path / "c.png"),
                    "--max-label-ratio", "1.5"]) == 1
    assert "FAIL: 0 labels for 1 speakers (no speaker label produced" in capsys.readouterr().out


def test_cli_fails_when_too_few_speakers_scored(tmp_path, monkeypatch, capsys):
    tags = [("a", 0), ("b", 1)]
    run_cli(tmp_path, monkeypatch, tags, 1.5)
    capsys.readouterr()
    assert ed.main([str(tmp_path), str(tmp_path / "l.csv"), "--png", str(tmp_path / "c.png"),
                    "--max-label-ratio", "1.5", "--min-speakers", "3"]) == 1
    assert "FAIL: 2 labels for 2 speakers (only 2 speakers scored, need 3" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--max-label-ratio", "nan"], ["--max-label-ratio", "-1"],
                                  ["--transcript", "x.jsonl", "--max-label-ratio", "1.5"]])
def test_cli_rejects_bad_ratio_use(args):
    with pytest.raises(SystemExit) as e:
        ed.main(["d", "l.csv", *args] if args[0] != "--transcript" else args)
    assert e.value.code == 2


def test_cli_without_ratio_only_reports(tmp_path, monkeypatch, capsys):
    assert run_cli(tmp_path, monkeypatch, [("a", 0), ("a", 3), ("a", 1)], 0) == 0
    out = capsys.readouterr().out
    assert "ratio: 3.00" in out and "FAIL" not in out and "PASS" not in out
