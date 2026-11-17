"""#1351: error breakdown of the lowest-COMET FLORES lines and the experiment variants of scripts/bench/flores_cpu.py.
No model or network: the translator, Q8_0 fetch and COMET are stubs."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("bench_flores_cpu_1351", ROOT / "scripts" / "bench" / "flores_cpu.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def row(i, src, ref, hyp, comet):
    return {"i": i, "src": src, "ref": ref, "hyp": hyp, "comet": comet}


@pytest.mark.parametrize("src,ref,hyp,want", [
    ("В 2019 году было 15 000 человек.", "In 2019 there were 15,000 people.", "In 2019 there were many people.",
     ["numbers"]),
    ("Он сказал, что Путин приехал.", "He said that Putin arrived.", "He said that Pootin arrived.",
     ["names", "name_miss"]),
    ("Кошка спит дома.", "貓在家睡覺。", "貓在Кош睡覺。", ["untranslated"]),
    ("Кошка спит дома весь день.", "The cat sleeps at home all day long.", "The cat.", ["omission"]),
    ("Кошка спит.", "The cat sleeps.", "The cat sleeps and the dog barks a lot.", ["addition"]),
    ("Кошка спит дома.", "The cat sleeps at home.", "The dog eats in the garden.", ["other"]),
    ("Москва большая.", "莫斯科很大。", "莫斯科很大。", ["other"]),  # sentence-initial capital is no named entity
    ("Он видел Москву.", "他看見了莫斯科。", "他看見了莫斯科。", ["names", "other"]),
])
def test_categories(src, ref, hyp, want):
    assert _load().categories(row(0, src, ref, hyp, 0.5)) == want


def test_long_source():
    src = " ".join(["слово"] * 31)
    assert "long" in _load().categories(row(0, src, "a b c", "a b c", 0.5))


def test_breakdown_counts_only_the_lowest_and_compares_with_all():
    m = _load()
    rows = [row(0, "В 2019 году.", "In 2019.", "In year.", 0.40),  # numbers, lowest
            row(1, "В 2020 году.", "In 2020.", "In 2020.", 0.95),
            row(2, "Кошка спит.", "The cat sleeps.", "The dog runs.", 0.50),  # other
            row(3, "Кошка спит.", "The cat sleeps.", "The cat sleeps.", 0.90),
            {"i": 4, "src": "x", "ref": "y", "hyp": "y"}]  # no COMET: ignored
    md = m.breakdown(rows, n=2, pair="rus_Cyrl->eng_Latn")
    assert "2 lowest-COMET of 4 sentences (mean COMET 0.450 vs 0.688 all)" in md
    assert "| numbers | 1 | 50% | 25% | 2.0 |" in md
    assert "| other | 1 | 50% | 75% | 0.7 |" in md
    assert "| 0 | 0.400 | numbers | В 2019 году. | In year. | In 2019. |" in md
    assert "| 3 |" not in md  # a high-COMET row is no example


def test_breakdown_without_comet():
    assert "no per-sentence COMET" in _load().breakdown([{"i": 0, "src": "a", "ref": "b", "hyp": "c"}], pair="p")


def test_breakdown_cli_reads_out_dir(tmp_path):
    (tmp_path / "rus_Cyrl-eng_Latn.jsonl").write_text(
        json.dumps(row(0, "В 2019 году.", "In 2019.", "In year.", 0.4), ensure_ascii=False) + "\n", "utf-8")
    (tmp_path / "rus_Cyrl-eng_Latn.summary.json").write_text(json.dumps({"pair": "rus_Cyrl->eng_Latn"}), "utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench" / "flores_cpu.py"), "--breakdown",
                        str(tmp_path), "5"], capture_output=True, text=True, encoding="utf-8", check=True)
    assert r.stdout.startswith("#### rus_Cyrl->eng_Latn: 1 lowest-COMET of 1 sentences")
    assert "| numbers | 1 | 100% | 100% | 1.0 |" in r.stdout


def test_variants_list():
    m = _load()
    assert m.variants("card, src,src,q8") == ["card", "src", "q8"]
    assert m.variants("") == ["card"]
    with pytest.raises(SystemExit, match="unknown variants"):
        m.variants("card,q2")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench" / "flores_cpu.py"), "--variants", "card,src"],
                       capture_output=True, text=True, check=True)
    assert json.loads(r.stdout) == ["card", "src"]


def test_src_variant_names_the_source_language(monkeypatch):
    m = _load()
    from tinglongwu import hymt

    monkeypatch.setattr(hymt, "build_prompt", hymt.build_prompt)
    card = hymt.build_prompt
    m.apply_variant("src", types.SimpleNamespace(), Path("x"))
    m.apply_variant("src", types.SimpleNamespace(), Path("x"))  # twice: not wrapped twice
    p = hymt.build_prompt
    assert p("Привет", "ru", "zh").startswith("将以下俄语文本翻译成中文，")
    assert p("Привет", "ru", "en").startswith("Translate the following Russian text into English.\n")
    assert p("Привет", "ru", "en").endswith("\n\nПривет")
    # glossary terms keep the card's terminology form unchanged
    assert p("Привет", "ru", "zh", [("Привет", "你好")]) == card("Привет", "ru", "zh", [("Привет", "你好")])
    assert card("Привет", "ru", "zh").startswith("将以下文本翻译成中文")  # the shipped prompt itself is untouched


def test_q8_variant_swaps_the_gguf_and_card_changes_nothing(tmp_path):
    m = _load()
    tr = types.SimpleNamespace(path=tmp_path / "q4.gguf")
    m.apply_variant("card", tr, tmp_path, fetch=lambda d: 1 / 0)
    assert tr.path == tmp_path / "q4.gguf"
    m.apply_variant("q8", tr, tmp_path, fetch=lambda d: Path(d) / "hymt2-1.8b-q8" / m.Q8_FILE)
    assert tr.path == tmp_path / "hymt2-1.8b-q8" / "Hy-MT2-1.8B-Q8_0.gguf"


def test_main_passes_variant_and_records_it(tmp_path, monkeypatch):
    m = _load()
    d = tmp_path / "flores200_dataset" / "devtest"
    d.mkdir(parents=True)
    (d / "rus_Cyrl.devtest").write_text("один\n", "utf-8")
    (d / "eng_Latn.devtest").write_text("one\n", "utf-8")
    mod = types.ModuleType("sacrebleu.metrics")
    mod.CHRF = lambda: types.SimpleNamespace(corpus_score=lambda h, r: types.SimpleNamespace(score=1.0),
                                             sentence_score=lambda h, r: types.SimpleNamespace(score=1.0))
    monkeypatch.setitem(sys.modules, "sacrebleu", types.ModuleType("sacrebleu"))
    monkeypatch.setitem(sys.modules, "sacrebleu.metrics", mod)
    seen = {}

    def make(models_dir, **kw):
        seen.update(kw)
        return types.SimpleNamespace(translate=lambda t, s, g: types.SimpleNamespace(text="one"))

    out = tmp_path / "out"
    assert m.main(["--data", str(tmp_path / "flores200_dataset"), "--models-dir", "x", "--src", "rus_Cyrl", "--tgt",
                   "eng_Latn", "--out", str(out), "--no-comet", "--variant", "src"], make=make) == 0
    assert seen == {"zh_variant": "tw", "variant": "src"} or seen == {"variant": "src"}
    assert json.loads((out / "rus_Cyrl-eng_Latn.summary.json").read_text("utf-8"))["variant"] == "src"
    m.main(["--data", str(tmp_path / "flores200_dataset"), "--models-dir", "x", "--src", "rus_Cyrl", "--tgt",
            "eng_Latn", "--out", str(tmp_path / "q8"), "--no-comet", "--variant", "q8"], make=make)
    s = json.loads((tmp_path / "q8" / "rus_Cyrl-eng_Latn.summary.json").read_text("utf-8"))
    assert s["variant"] == "q8" and "Q8_0" in s["engine"]


def test_targets_list():
    m = _load()
    assert m.targets("") == ["zho_Hant"]
    assert m.targets("zho_Hant,eng_Latn") == ["zho_Hant", "eng_Latn"]
    with pytest.raises(SystemExit, match="unknown targets"):
        m.targets("xxx_Latn")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "bench" / "flores_cpu.py"), "--tgts", "eng_Latn"],
                       capture_output=True, text=True, check=True)
    assert json.loads(r.stdout) == ["eng_Latn"]


def test_q8_needs_the_downloaded_file_and_repo_matches_the_app(tmp_path):
    m = _load()
    from tinglongwu.hymt import HYMT_REPO

    assert m.HYMT_REPO == HYMT_REPO
    with pytest.raises(SystemExit, match="--fetch-q8"):
        m.apply_variant("q8", types.SimpleNamespace(path=None), tmp_path)
    m.q8_path(tmp_path).parent.mkdir(parents=True)
    m.q8_path(tmp_path).write_bytes(b"gguf")
    tr = m.apply_variant("q8", types.SimpleNamespace(path=None), tmp_path)
    assert tr.path == tmp_path / "hymt2-1.8b-q8" / "Hy-MT2-1.8B-Q8_0.gguf"


def test_prompt_echo_is_its_own_category_and_listed_in_full():
    m = _load()
    note = "Note that you should only output the translated result without any additional explanation:"
    d = row(84, "Вода выливается за плотину.", "Water is spilling over the levee.", note, 0.595)
    assert m.categories(d)[0] == "echo"
    md = m.breakdown([d, row(1, "Кошка спит.", "The cat sleeps.", "The cat sleeps.", 0.9)], n=1, pair="p")
    assert "| echo | 1 | 100% | 50% | 2.0 |" in md
    assert f"1 of 2 outputs echo the prompt; full text of the first 3:\n- 84: `{note}`" in md


def test_resume_refuses_another_variant(tmp_path):
    m = _load()
    jsonl = tmp_path / "rus_Cyrl.jsonl"
    tr = types.SimpleNamespace(translate=lambda t, s, g: types.SimpleNamespace(text="x"))
    m.translate_all([("a", "b")], tr, "ru", jsonl, variant="src")
    assert json.loads(jsonl.read_text("utf-8"))["variant"] == "src"
    with pytest.raises(SystemExit, match="variant src, not q8"):
        m.translate_all([("a", "b")], tr, "ru", jsonl, variant="q8")
    assert len(m.translate_all([("a", "b")], tr, "ru", jsonl, variant="src")) == 1  # same variant resumes


def test_7b_variant_loads_the_opt_in_7b_model(tmp_path, monkeypatch):
    """#1351 r2: variant 7b builds HyMtTranslator(size="7b") (the `tlwu-setup --hymt7b` file) and records it."""
    m = _load()
    sys.path.insert(0, str(ROOT))
    from tinglongwu import hymt

    got = {}

    class Fake:
        def __init__(self, models_dir, zh_variant, device, size="1.8b"):
            got.update(size=size, path=hymt.hymt_path(models_dir, size))
            self.path = got["path"]

        def preload(self):
            pass

    monkeypatch.setattr(hymt, "HyMtTranslator", Fake)
    tr = m.make_translator(tmp_path, variant="7b")
    assert got["size"] == "7b" and tr.path.name == "Hy-MT2-7B-Q4_K_M.gguf"
    m.make_translator(tmp_path, variant="card")
    assert got["size"] == "1.8b"
    assert m.engine_name("7b") == "Hy-MT2-7B Q4_K_M (llama.cpp, CPU)"
    assert m.engine_name("q8") == "Hy-MT2-1.8B Q8_0 (llama.cpp, CPU)"
    assert m.variants("card,7b") == ["card", "7b"]


def test_worst_dir_keeps_lowest_comet_per_direction_1346(tmp_path):
    fc = _load()
    for pair, base in (("rus_Cyrl-eng_Latn", 0.5), ("rus_Cyrl-zho_Hans", 0.6)):
        rows = [dict(row(i, f"s{i}", f"r{i}", f"h{i}", round(base + i / 100, 3)), chrf=1.0) for i in range(30)]
        (tmp_path / f"{pair}.jsonl").write_text("\n".join(json.dumps(r) for r in reversed(rows)), "utf-8")
        (tmp_path / f"{pair}.summary.json").write_text(json.dumps({"pair": pair}), "utf-8")
    res = fc.worst_dir([str(tmp_path)], 20, {"run_id": "1"})
    assert res["run"] == {"run_id": "1"}
    assert set(res["directions"]) == {"rus_Cyrl-eng_Latn", "rus_Cyrl-zho_Hans"}
    for rows in res["directions"].values():
        assert len(rows) == 20
        assert [r["i"] for r in rows] == list(range(20))
        assert set(rows[0]) == {"i", "src", "ref", "hyp", "comet", "chrf"}


def test_worst_cli_writes_json_1346(tmp_path):
    (tmp_path / "a.jsonl").write_text(json.dumps(row(0, "s", "r", "h", 0.1)), "utf-8")
    out = tmp_path / "w.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts/bench/flores_cpu.py"), "--worst", str(tmp_path), "5", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert len(json.loads(out.read_text("utf-8"))["directions"]["a"]) == 1


# #1351 r3: other-family GGUF candidates (Gemma 3 12B, Qwen3 8B) through llama.cpp's chat template; stub llm/fetch.
class _Llm:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, **kw):
        self.calls.append((messages, kw))
        return {"choices": [{"message": {"content": self.reply}}]}


def test_variants_accept_other_families():
    assert _load().variants("card,gemma3-12b,qwen3-8b") == ["card", "gemma3-12b", "qwen3-8b"]


def test_chat_gguf_qwen3_thinking_off_and_stripped(monkeypatch):
    m = _load()
    monkeypatch.delenv("TLWU_HYMT_TEMP", raising=False)
    llm = _Llm("<think>\n\n</think>\n\nThe cat sleeps.")
    tr = m.ChatGguf(Path("x.gguf"), "qwen3-8b", llm=llm, convert=lambda t, v: t)
    assert tr.translate("Кошка спит.", "ru", "en").text == "The cat sleeps."
    (msgs, kw), = llm.calls
    assert msgs[0]["content"].startswith("Translate the following Russian text into English.")
    assert msgs[0]["content"].endswith("Кошка спит. /no_think")
    assert kw["temperature"] == 0.0 and kw["top_k"] == 1


def test_chat_gguf_zh_goes_through_zh_convert_like_hymt():
    m = _load()
    llm = _Llm("Translation: 猫在睡觉。")
    seen = []
    tr = m.ChatGguf(Path("x.gguf"), "gemma3-12b", "tw", llm=llm, convert=lambda t, v: seen.append(v) or "貓在睡覺。")
    assert tr.translate("Кошка спит.", "ru", "zh").text == "貓在睡覺。"
    assert seen == ["tw"]
    assert "Simplified Chinese" in llm.calls[0][0][0]["content"]
    assert "/no_think" not in llm.calls[0][0][0]["content"]


def test_fetch_gguf_uses_the_pinned_repo_and_rejects_unknown(tmp_path):
    m = _load()
    got = []

    def dl(repo, fname, local_dir):
        got.append((repo, fname, local_dir))
        return str(Path(local_dir) / fname)

    info = lambda repo: types.SimpleNamespace(card_data={"license": "apache-2.0"})  # noqa: E731
    p = m.fetch_gguf(tmp_path, "qwen3-8b", download=dl, info=info)
    assert got == [("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", str(tmp_path / "bench-qwen3-8b"))]
    assert p == m.gguf_path(tmp_path, "qwen3-8b")
    with pytest.raises(SystemExit):
        m.fetch_gguf(tmp_path, "7b", download=dl, info=info)


def test_make_translator_needs_the_fetched_gguf(tmp_path):
    m = _load()
    with pytest.raises(SystemExit, match="--fetch-gguf"):
        m.make_translator(tmp_path, variant="gemma3-12b")
    assert m.engine_name("gemma3-12b") == "gemma-3-12b-it-Q4_K_M.gguf (llama.cpp, CPU)"


@pytest.mark.parametrize("reply,want", [
    ('Translation: "The cat sleeps."', "The cat sleeps."),  # label + matched quotes, like Hy-MT2's clean_output
    ("<think>\n\n</think>\n\n«Кот»", "Кот"),
    ('He said "hi" to her.', 'He said "hi" to her.'),  # inner quotes stay
])
def test_chat_output_matches_the_hymt_cleanup(reply, want):
    assert _load().chat_output(reply) == want
