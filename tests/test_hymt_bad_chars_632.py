"""#632: Hy-MT2 output with box characters (U+FFFD, private use) is retried greedily once, then stripped."""
from tinglongwu.hymt import HyMtTranslator, bad_chars, hymt_path, strip_bad_chars


class SeqLlm:
    """Fake llama: returns `replies` in order (the last one repeats) and records each call's kwargs."""

    def __init__(self, *replies):
        self.replies, self.kw = list(replies), []

    def create_chat_completion(self, messages, **kw):
        self.kw.append(kw)
        reply = self.replies[min(len(self.kw), len(self.replies)) - 1]
        return {"choices": [{"message": {"content": reply}}]}


def make(tmp_path, llm):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    return HyMtTranslator(tmp_path, "tw", "cpu", loader=lambda path, gpu: llm)


def test_bad_chars_counts_boxes_not_cjk():
    assert bad_chars("老師！你好，對了！不錯！") == 0
    assert bad_chars("老師！n�氮�，對了！") == 2
    assert bad_chars("頓") == 1 and bad_chars("a\x01b") == 1 and bad_chars("𠀀𪚥") == 0  # Ext-B is fine
    assert strip_bad_chars("頓� 了") == "頓 了"


def test_replacement_char_triggers_one_greedy_retry(tmp_path):
    llm = SeqLlm("老師！n�氮�，對了！", "老師！我知道答案！")
    tr = make(tmp_path, llm)
    out = tr.translate("Teacher! I know the answer!", "en", "zh").text
    assert out == "老師！我知道答案！"
    assert len(llm.kw) == 2 and llm.kw[0]["temperature"] == 0.7 and llm.kw[1]["temperature"] == 0.0
    assert tr.retries == 1


def test_still_bad_after_retry_is_stripped(tmp_path):
    llm = SeqLlm("頓�", "頓�")
    tr = make(tmp_path, llm)
    assert tr.translate("Hey ma'am.", "en", "zh").text == "頓"
    assert len(llm.kw) == 2


def test_clean_output_makes_no_retry_and_seed_is_passed(tmp_path):
    llm = SeqLlm("你好。")
    tr = make(tmp_path, llm)
    assert tr.translate("Hello.", "en", "zh").text == "你好。"
    assert len(llm.kw) == 1 and "seed" not in llm.kw[0]
    tr.seed = 7
    tr.translate("Hello again.", "en", "zh")
    assert llm.kw[-1]["seed"] == 7


def test_fffd_repro_counts_before_and_after(tmp_path):
    """scripts/bench/fffd_repro.py: a sampled box output counts in `before`, the greedy retry clears `after`."""
    import importlib.util
    import json

    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "bench" / "fffd_repro.py"
    spec = importlib.util.spec_from_file_location("fffd_repro", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class SeedLlm:
        def create_chat_completion(self, messages, **kw):
            bad = kw.get("seed") == 3 and kw["temperature"] > 0
            return {"choices": [{"message": {"content": "頓�" if bad else "嘿，女士。"}}]}

    out = tmp_path / "out"
    assert mod.main(["--models-dir", str(tmp_path), "--out", str(out), "--seeds", "5"],
                    make=lambda md: make(tmp_path, SeedLlm())) == 0
    s = json.loads((out / "fffd.summary.json").read_text("utf-8"))
    assert s["screenshot"] == {"n": 20, "before": 4, "after": 0} and s["reproduced"] and s["retries"] == 4
    rows = [json.loads(x) for x in (out / "fffd.jsonl").read_text("utf-8").splitlines()]
    assert len(rows) == 4 and all(r["seed"] == 3 and r["out"] == "嘿，女士。" for r in rows)
