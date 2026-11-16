"""#745: cpu_mode_ab.py variants + per-clip diff, and tlwu_eval --beam / --asr-threads overrides."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("cpu_mode_ab_745", Path(__file__).resolve().parents[1] / "scripts/bench/cpu_mode_ab.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def report(clips, chrf=40.0, err=0.05):
    return {"languages": [{"pair": "en->zh", "chrf": chrf, "error_rate": err,
                           "clips": [dict(c, latency_s=c.get("latency_s", 2.0)) for c in clips]}]}


A = report([{"id": "en_001", "asr": "To the north, and within easy reach, is Cintra.", "mt": "向北，近在咫尺的是辛特拉。"},
            {"id": "en_002", "asr": "Cabbage juice changes color.", "mt": "捲心菜汁會變色。"}])
B = report([{"id": "en_001", "asr": "To the north and within easy reach is Cintra.", "mt": "向北，距離很近的是辛特拉。"},
            {"id": "en_002", "asr": "Cabbage juice changes color.", "mt": "捲心菜汁會變色。"}], chrf=36.0)


def test_clip_diffs_finds_only_the_changed_clip():
    d = ab.clip_diffs(A, B)
    assert [x["id"] for x in d] == ["en_001"]
    assert d[0]["asr"] == ("To the north, and within easy reach, is Cintra.", "To the north and within easy reach is Cintra.")
    assert d[0]["mt"][1] == "向北，距離很近的是辛特拉。"
    assert ab.clip_diffs(A, A) == []


def test_diff_report_counts_and_lists():
    out = ab.diff_report(A, B)
    assert out.splitlines()[0] == "clips with different ASR text: 1/2; different MT text: 1/2"
    assert "- en->zh en_001 ASR:" in out and "- en->zh en_001 MT:" in out
    assert "en_002" not in out


def test_load_variants(tmp_path):
    assert ab.load_variants(tmp_path / "missing.json") == []
    f = tmp_path / "v.json"
    f.write_text(json.dumps([{"name": "b1", "args": "--beam 1"}, {"name": "t4", "args": "--asr-threads 4"}]))
    assert ab.load_variants(f) == [("b1", "--beam 1"), ("t4", "--asr-threads 4")]
    for bad in ([{"name": "x", "args": ""}, {"name": "x", "args": ""}], [{"name": "cpu", "args": ""}], {"name": "x"},
                [{"args": "--beam 1"}]):
        f.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            ab.load_variants(f)


def test_run_with_variants_prints_one_table_per_variant(tmp_path, monkeypatch, capsys):
    reports = {"base": A, "cpu": B, "b1": B, "t4": A}
    calls = []

    def fake_run_eval(extra, data, out_json, out_jsonl, run=None):
        calls.append((out_json.stem, extra))
        out_json.write_text(json.dumps(reports[out_json.stem]), "utf-8")

    monkeypatch.setattr(ab, "run_eval", fake_run_eval)
    v = tmp_path / "variants.json"
    v.write_text(json.dumps([{"name": "b1", "args": "--beam 1"}, {"name": "t4", "args": "--asr-threads 4"}]))
    rc = ab.main(["--run", "--out-dir", str(tmp_path / "ab"), "--cpu-args=--cpu-mode", "--variants", str(v)])
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [("base", ""), ("cpu", "--cpu-mode"), ("b1", "--beam 1"), ("t4", "--asr-threads 4")]
    assert "### variant b1 vs defaults" in out and "### variant t4 vs defaults" in out
    assert out.count("p50 drop") == 3
    # the t4 variant has the same text as A: no changed clips
    assert out.split("### variant t4 vs defaults")[1].count("different MT text: 0/2") == 1


def test_run_without_variants_file_runs_two_evals(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_run_eval(extra, data, out_json, out_jsonl, run=None):
        calls.append(out_json.stem)
        out_json.write_text(json.dumps({"base": A, "cpu": B}[out_json.stem]), "utf-8")

    monkeypatch.setattr(ab, "run_eval", fake_run_eval)
    monkeypatch.setattr(ab, "VARIANTS_FILE", tmp_path / "none.json")  # default file absent: no variants
    rc = ab.main(["--run", "--out-dir", str(tmp_path / "ab"), "--cpu-args=--cpu-mode"])
    assert rc == 0 and calls == ["base", "cpu"]
    assert "### variant" not in capsys.readouterr().out


def test_bad_variants_file_exits_2(tmp_path, capsys):
    v = tmp_path / "v.json"
    v.write_text("{not json")
    assert ab.main(["--run", "--out-dir", str(tmp_path), "--cpu-args=--cpu-mode", "--variants", str(v)]) == 2


def test_eval_asr_overrides_win_over_cpu_mode():
    from tlwu_eval.__main__ import asr_overrides

    none = SimpleNamespace(beam=None, asr_threads=None)
    assert asr_overrides(none, 1, 3) == (1, 3)
    assert asr_overrides(none, None, None) == (None, None)
    assert asr_overrides(SimpleNamespace(beam=5, asr_threads=None), 1, 3) == (5, 3)
    assert asr_overrides(SimpleNamespace(beam=None, asr_threads=4), 1, 3) == (1, 4)


def test_real_engines_hands_overrides_to_the_recognizer(monkeypatch):
    import tinglongwu.asr
    import tinglongwu.device
    import tinglongwu.models
    from tlwu_eval import __main__ as ev

    seen = {}

    class FakeRec:
        def __init__(self, mdir, model, lang, dev, beam=None, threads=None):
            seen.update(model=model, beam=beam, threads=threads)

    monkeypatch.setattr(tinglongwu.asr, "FasterWhisperRecognizer", FakeRec)
    monkeypatch.setattr(tinglongwu.device, "resolve_device", lambda d: "cpu")
    monkeypatch.setattr(tinglongwu.models, "models_dir", lambda d: Path("m"))
    monkeypatch.setattr(tinglongwu.models, "pick_whisper", lambda size, mdir: size)
    a = SimpleNamespace(models_dir=None, device="cpu", model=None, engine="auto", cpu_mode=False, beam=1, asr_threads=4)
    ev.real_engines(a).load_asr("en")
    assert seen == {"model": "small", "beam": 1, "threads": 4}


def test_cli_accepts_beam_and_asr_threads(capsys):
    from tlwu_eval.__main__ import main

    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--beam N" in out and "--asr-threads N" in out


def test_clip_diffs_rejects_different_clip_sets():
    one = report([{"id": "en_001", "asr": "a", "mt": "b"}])
    with pytest.raises(ValueError, match="only in A en->zh en_002"):
        ab.clip_diffs(A, one)


def test_variant_names_cannot_alias_other_outputs(tmp_path):
    f = tmp_path / "v.json"
    for name in ("./base", "../x", "a/b", ".hidden", ""):
        f.write_text(json.dumps([{"name": name, "args": ""}]))
        with pytest.raises(ValueError):
            ab.load_variants(f)
    f.write_text(json.dumps([{"name": "small-b1_t4.v2", "args": ""}]))
    assert ab.load_variants(f) == [("small-b1_t4.v2", "")]


def test_run_with_mismatched_variant_clips_exits_2(tmp_path, monkeypatch, capsys):
    one = report([{"id": "en_001", "asr": "a", "mt": "b"}])

    def fake_run_eval(extra, data, out_json, out_jsonl, run=None):
        out_json.write_text(json.dumps({"base": A, "cpu": B, "few": one}[out_json.stem]), "utf-8")

    monkeypatch.setattr(ab, "run_eval", fake_run_eval)
    v = tmp_path / "variants.json"
    v.write_text(json.dumps([{"name": "few", "args": "--per-lang 1"}]))
    assert ab.main(["--run", "--out-dir", str(tmp_path / "ab"), "--cpu-args=--cpu-mode", "--variants", str(v)]) == 2
    assert "different clips" in capsys.readouterr().err


def test_explicit_missing_variants_file_exits_2(tmp_path, capsys):
    rc = ab.main(["--run", "--out-dir", str(tmp_path), "--cpu-args=--cpu-mode", "--variants", str(tmp_path / "nope.json")])
    assert rc == 2 and "variants file not found" in capsys.readouterr().err


def test_reserved_names_are_case_insensitive(tmp_path):
    f = tmp_path / "v.json"
    for name in ("BASE", "Cpu"):
        f.write_text(json.dumps([{"name": name, "args": ""}]))
        with pytest.raises(ValueError):
            ab.load_variants(f)


def test_duplicate_clip_ids_are_rejected():
    dup = report([{"id": "en_001", "asr": "a", "mt": "b"}, {"id": "en_001", "asr": "c", "mt": "d"}])
    with pytest.raises(ValueError, match="duplicate clip en->zh en_001"):
        ab.clip_diffs(dup, dup)


def test_load_variants_rejects_names_differing_only_in_case(tmp_path):
    # #745 leftover (CodeRabbit r4583247136): "small" and "SMALL" are one output file on Windows / macOS
    f = tmp_path / "v.json"
    f.write_text(json.dumps([{"name": "small", "args": "--beam 1"}, {"name": "SMALL", "args": "--beam 2"}]), "utf-8")
    with pytest.raises(ValueError, match="unique"):
        ab.load_variants(f)
