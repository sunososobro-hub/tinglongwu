"""#1319: scripts/bench/mt_candidates.py (CPU A/B of smaller MT models vs Hy-MT2 Q4_K_M), with fake models / hub."""
import importlib.util
import json
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "scripts/bench"
spec = importlib.util.spec_from_file_location("mt_candidates", BENCH / "mt_candidates.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

from tlwu_eval.scoring import chrf  # noqa: E402

import cpu_mode_ab  # noqa: E402  (scripts/bench is on sys.path once mt_candidates is loaded)


def clip(i, ref, mt, asr_s=1.0, mt_s=0.5):
    return {"id": f"c{i:02d}", "src_text": "src", "ref": ref, "mt": mt, "asr_s": asr_s, "mt_s": mt_s}


def result(mts, mt_s=0.5, **kw):
    refs = ["今天天氣很好", "我們明天見面", "這是一個測試句子", "火車晚了十分鐘"]
    return {"languages": [{"pair": "en->zh", "clips": [clip(i, r, m, mt_s=mt_s) for i, (r, m) in enumerate(zip(refs, mts))]}],
            "repo": "x/y", "quant": "Q4_K_M", "license": "Apache-2.0", "size_mb": 400.0, **kw}


def test_parse_names_defaults_and_rejects_typos():
    assert mc.parse_names("") == list(mc.CANDIDATES)
    assert mc.parse_names(" qwen3-0.6b ,") == ["qwen3-0.6b"]
    with pytest.raises(ValueError, match="qwen9"):
        mc.parse_names("qwen9")


def test_every_candidate_is_open_and_known_format():
    for c in mc.CANDIDATES.values():
        assert c["license"] in ("Apache-2.0", "MIT")
        mc.chat_prompt("x", c["fmt"])  # raises on an unknown format


def test_chat_prompt_formats():
    p = mc.chat_prompt("Translate: hi", "chatml")
    assert p == "<|im_start|>user\nTranslate: hi<|im_end|>\n<|im_start|>assistant\n"
    assert mc.chat_prompt("x", "chatml-nothink").endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    with pytest.raises(ValueError):
        mc.chat_prompt("x", "llama3")


def test_select_clips_per_pair_sorted_and_needs_reference():
    rows = [{"id": "en_003", "lang": "en", "ref_zh": "c"}, {"id": "en_001", "lang": "en", "ref_zh": "a"},
            {"id": "en_002", "lang": "en"}, {"id": "zh_001", "lang": "zh", "ref_en": "z"}]
    got = mc.select_clips(rows, [("en", "zh"), ("zh", "en")], 2)
    assert [(s, t, r["id"]) for s, t, r in got] == [("en", "zh", "en_001"), ("en", "zh", "en_003"), ("zh", "en", "zh_001")]


def test_chrf_ci_brackets_the_score():
    pairs = [("今天天氣很好", "今天天氣不錯"), ("我們明天見面", "我們明天見"), ("火車晚了", "火車遲到了")]
    s, lo, hi = mc.chrf_ci(pairs, chrf)
    assert lo <= s <= hi and hi > lo
    assert mc.chrf_ci([], chrf) is None
    assert mc.chrf_ci([("同樣", "同樣")] * 3, chrf) == (100.0, 100.0, 100.0)


def test_translate_all_times_lines_and_skips_empty_asr():
    calls = []

    class FakeMT:
        def translate(self, text, src, tgt):
            calls.append(text)
            return f" <{text}> ", 0.25

    lines = [{"pair": "en->zh", "id": "a", "src": "en", "tgt": "zh", "ref": "r1", "asr": "hello", "asr_s": 1.0},
             {"pair": "en->zh", "id": "b", "src": "en", "tgt": "zh", "ref": "r2", "asr": "", "asr_s": 2.0},
             {"pair": "zh->en", "id": "c", "src": "zh", "tgt": "en", "ref": "r3", "asr": "你好", "asr_s": 1.5}]
    res = mc.translate_all(lines, FakeMT())
    assert calls == ["hello", "hello", "你好"]  # warm-up + two real lines; the empty ASR line is not translated
    assert [lg["pair"] for lg in res["languages"]] == ["en->zh", "zh->en"]
    a, b = res["languages"][0]["clips"]
    assert a["mt"] == "<hello>" and a["mt_s"] == 0.25 and b["mt"] == "" and b["mt_s"] == 0.0


def test_llama_mt_baseline_uses_app_prompt_and_candidate_uses_chat_format():
    seen = {}

    class FakeLlama:
        def __init__(self, **kw):
            seen["init"] = kw

        def create_chat_completion(self, messages, **kw):
            seen["chat"] = (messages, kw)
            return {"choices": [{"message": {"content": "汉语"}}]}

        def create_completion(self, prompt, **kw):
            seen["completion"] = (prompt, kw)
            return {"choices": [{"text": "<think></think>Hello there"}]}

    ticks = iter([0.0, 1.5, 10.0, 10.25])
    base = mc.LlamaMT(Path("m.gguf"), None, 2, 3, llama=FakeLlama)
    assert seen["init"]["n_threads"] == 2 and seen["init"]["n_threads_batch"] == 3 and seen["init"]["n_gpu_layers"] == 0
    text, dt = base.translate("Chinese", "en", "zh", clock=lambda: next(ticks))
    assert text == "漢語" and dt == 1.5  # OpenCC to Taiwan script, like the app
    assert "Chinese" in seen["chat"][0][0]["content"]
    cand = mc.LlamaMT(Path("m.gguf"), "chatml-nothink", 2, 2, llama=FakeLlama)
    text, dt = cand.translate("你好", "zh", "en", clock=lambda: next(ticks))
    assert text == "Hello there" and dt == 0.25
    prompt, kw = seen["completion"]
    assert prompt.startswith("<|im_start|>user\n") and "你好" in prompt and kw["temperature"] == 0.0
    assert "<|im_end|>" in kw["stop"]


def test_fetch_picks_the_quant_file(tmp_path):
    class Hub:
        downloads = []

        @staticmethod
        def list_repo_files(repo):
            return {"Qwen/Qwen2.5-0.5B-Instruct-GGUF": ["README.md", "qwen2.5-0.5b-instruct-q4_k_m.gguf",
                                                        "qwen2.5-0.5b-instruct-q8_0.gguf"],
                    "Qwen/Qwen3-0.6B-GGUF": ["Qwen3-0.6B-Q8_0.gguf"],
                    "Qwen/Qwen2.5-1.5B-Instruct-GGUF": ["qwen2.5-1.5b-instruct-q8_0.gguf"]}[repo]

        @classmethod
        def hf_hub_download(cls, repo, f, local_dir):
            cls.downloads.append((repo, f))
            return str(Path(local_dir) / f)

    got = mc.fetch(["qwen2.5-0.5b", "qwen3-0.6b", "qwen2.5-1.5b"], tmp_path, hub=Hub)
    assert got["qwen2.5-0.5b"].endswith("qwen2.5-0.5b-instruct-q4_k_m.gguf")
    assert got["qwen3-0.6b"].endswith("Qwen3-0.6B-Q8_0.gguf")
    assert got["qwen2.5-1.5b"] is None  # the fake 1.5B repo has no Q4_K_M file
    assert ("Qwen/Qwen2.5-0.5B-Instruct-GGUF", "qwen2.5-0.5b-instruct-q4_k_m.gguf") in Hub.downloads


def test_table_rows_and_paired_change_vs_baseline():
    base = result(["今天天氣很好", "我們明天見面", "這是一個測試句子", "火車晚了十分鐘"], mt_s=2.0)
    cand = result(["今天天氣好", "我們明天見", "這是測試", "火車晚了"], mt_s=0.5)
    t = mc.table({mc.BASELINE: base, "qwen2.5-0.5b": cand}, chrf, cpu_mode_ab.chrf_drop_ci)
    assert f"| {mc.BASELINE} | en->zh | 2000 | 3.00 | 100.00 [100.00, 100.00] | n/a | 4 |" in t
    assert "| qwen2.5-0.5b | en->zh | 500 | 1.50 |" in t
    row = next(ln for ln in t.splitlines() if ln.startswith("| qwen2.5-0.5b | x/y"))
    assert "| -75% | -50% |" in row  # MT 2000 -> 500 ms, end to end 3.0 -> 1.5 s
    change = row.rsplit("|", 2)[1].strip()
    assert change.startswith("-") and "[" in change  # lower chrF than the exact baseline, with an interval


def test_table_needs_baseline_and_same_pairs():
    cand = result(["a", "b", "c", "d"])
    with pytest.raises(ValueError, match="no hymt2"):
        mc.table({"qwen2.5-0.5b": cand}, chrf, cpu_mode_ab.chrf_drop_ci)
    other = json.loads(json.dumps(cand))
    other["languages"][0]["pair"] = "ja->zh"
    with pytest.raises(ValueError, match="different pairs"):
        mc.table({mc.BASELINE: cand, "qwen3-0.6b": other}, chrf, cpu_mode_ab.chrf_drop_ci)


def test_comet_scores_are_added_and_shown(tmp_path):
    class FakeComet:
        def predict(self, data, **kw):
            return type("P", (), {"scores": [0.8 for _ in data]})()

    (tmp_path / f"{mc.BASELINE}.json").write_text(json.dumps(result(["今天", "我們", "這是", "火車"])), "utf-8")
    assert mc.add_comet(tmp_path, FakeComet()) == [mc.BASELINE]
    saved = json.loads((tmp_path / f"{mc.BASELINE}.json").read_text("utf-8"))
    assert saved["languages"][0]["comet"] == 80.0 and saved["languages"][0]["clips"][0]["comet"] == 80.0


def test_main_table_from_dir(tmp_path, capsys):
    (tmp_path / f"{mc.BASELINE}.json").write_text(json.dumps(result(["今天天氣很好", "我們", "這是", "火車"])), "utf-8")
    (tmp_path / "qwen3-0.6b.json").write_text(json.dumps(result(["今天", "我們", "這是", "火車"], mt_s=0.1)), "utf-8")
    assert mc.main(["--table", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "| qwen3-0.6b | **all** | 100 |" in out
    (tmp_path / f"{mc.BASELINE}.json").unlink()
    assert mc.main(["--table", "--dir", str(tmp_path)]) == 2


def test_missing_clips_names_every_underfilled_pair():
    rows = [{"id": f"en_{i}", "lang": "en", "ref_zh": "x"} for i in range(3)] + [{"id": "ja_1", "lang": "ja", "ref_zh": "y"}]
    pairs = [("en", "zh"), ("ja", "zh"), ("ru", "zh")]
    got = mc.select_clips(rows, pairs, 3)
    assert mc.missing_clips(got, pairs, 3) == {"ja->zh": 2, "ru->zh": 3}
    assert mc.missing_clips(got, pairs[:1], 3) == {}
