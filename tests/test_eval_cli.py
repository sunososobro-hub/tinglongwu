"""#101 part 2b: `python -m tlwu_eval --smoke` CLI entry (report file, printed summary, exit codes)."""
import json
import subprocess
import sys

import pytest

from tlwu_eval import runner
from tlwu_eval.__main__ import main

from test_eval_smoke import Clock, fake_engines, make_data


def test_cli_writes_report_and_exit_codes(tmp_path, capsys):
    data = make_data(tmp_path, langs=("en", "ru"))
    clock = Clock()
    out = tmp_path / "r.json"
    code = main(["--smoke", "--data", str(data), "--out", str(out)], engines=fake_engines(clock), totals=(16000, None),
                clock=clock)
    printed = capsys.readouterr().out
    assert code == 0 and "smoke: PASS" in printed and "en->zh: load 1.0s | ASR 2.0s RTF 0.2 PASS" in printed and "WER 0.0%" in printed
    assert "ja->zh: SKIP" in printed
    rep = json.loads(out.read_text("utf-8"))
    assert rep["pass"] is True and rep["skipped"] == ["ja->zh", "zh->en"] and rep["thresholds"]["asr_rtf_max"] == 1.0
    clock = Clock()
    assert main(["--smoke", "--data", str(data), "--out", str(out)], engines=fake_engines(clock, mt_s=3.0),
                totals=(16000, None), clock=clock) == 1
    assert "smoke: FAIL" in capsys.readouterr().out


def test_missing_data_is_a_clear_setup_error(tmp_path):
    r = subprocess.run([sys.executable, "-m", "tlwu_eval", "--smoke", "--data", str(tmp_path / "nope"),
                        "--out", str(tmp_path / "r.json")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "manifest.jsonl is missing" in r.stderr and "Traceback" not in r.stderr
    (tmp_path / "manifest.jsonl").write_text('{"id": "a", "lang": "en"}\n', "utf-8")
    with pytest.raises(ValueError, match="missing wav, ref_text"):
        runner.load_manifest(tmp_path)


def test_cli_runs_external_nhk_ja_group_and_kana_turns_it_red(tmp_path, capsys):
    data = make_data(tmp_path / "d", langs=("en",)) if (tmp_path / "d").mkdir() is None else None
    ext = tmp_path / "ext"
    ext.mkdir()
    make_data(ext, langs=("ja",), n=2)
    out = tmp_path / "r.json"
    args = ["--smoke", "--data", str(data), "--out", str(out), "--nhk-dir", str(ext)]
    clock = Clock()
    assert main(args, engines=fake_engines(clock), totals=(16000, None), clock=clock) == 0
    rep = json.loads(out.read_text("utf-8"))
    nhk = next(lg for lg in rep["languages"] if lg["pair"] == "ja->zh[nhk]")
    assert len(nhk["clips"]) == 2 and nhk["kana_rate"] == 0.0
    assert "ja->zh[nhk]:" in capsys.readouterr().out
    clock = Clock()
    assert main(args, engines=fake_engines(clock, mt_text="貓さん"), totals=(16000, None), clock=clock) == 1
    assert "kana 100.00 FAIL" in capsys.readouterr().out
    # folder absent -> group silently not run (default smoke unchanged)
    clock = Clock()
    assert main(args[:-1] + [str(tmp_path / "none")], engines=fake_engines(clock), totals=(16000, None), clock=clock) == 0
    assert all(lg["pair"] != "ja->zh[nhk]" for lg in json.loads(out.read_text("utf-8"))["languages"])
