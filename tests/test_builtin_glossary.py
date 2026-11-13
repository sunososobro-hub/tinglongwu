"""#256: built-in tech glossary, Hy-MT2 terminology prompt, configurable temperature (fake model, no device)."""
from tinglongwu import glossary, menuopts
from tinglongwu.hymt import HyMtTranslator, build_prompt, hymt_path, sampling


class FakeLlm:
    def __init__(self):
        self.calls = []

    def create_chat_completion(self, messages, **kw):
        self.calls.append((messages[0]["content"], kw))
        return {"choices": [{"message": {"content": "好"}}]}


def make(tmp_path, g=None):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    llm = FakeLlm()
    tr = HyMtTranslator(tmp_path, "tw", "cpu", loader=lambda p, g: llm)
    tr.glossary = g
    return tr, llm


def test_builtin_has_50_entries_with_required_terms():
    g = glossary.load_builtin()
    assert len(g.entries) >= 50
    mains = {e.main for e in g.entries}
    for w in "daemon driver header toolchain kernel commit merge build release".split():
        assert w in mains


def test_user_entries_win_over_builtin():
    user = glossary.Glossary(glossary.parse("daemon = 守護行程"))
    m = glossary.merge(user, glossary.load_builtin())
    assert [e.target for e in m.entries if e.main == "daemon"] == ["守護行程"]
    assert len(m.entries) == len(glossary.load_builtin().entries)


def test_prompt_has_terms_only_on_hit(tmp_path):
    tr, llm = make(tmp_path, glossary.load_builtin())
    tr.translate("The daemon crashed after the merge.", "en", "zh")
    p, _ = llm.calls[-1]
    assert "daemon 翻译成 常駐程式" in p and "merge 翻译成 合併" in p
    tr.translate("Hello there my friend.", "en", "zh")  # #535: the previous 2 lines also count
    assert "daemon 翻译成 常駐程式" in llm.calls[-1][0]
    tr.translate("Nice weather today.", "en", "zh")
    tr.translate("See you soon.", "en", "zh")
    assert llm.calls[-1][0] == build_prompt("See you soon.", "en", "zh")
    tr.glossary = None
    tr.translate("The daemon crashed.", "en", "zh")
    assert llm.calls[-1][0] == build_prompt("The daemon crashed.", "en", "zh")


def test_temperature_env(monkeypatch):
    monkeypatch.delenv("TLWU_HYMT_TEMP", raising=False)
    assert sampling()["temperature"] == 0.7
    monkeypatch.setenv("TLWU_HYMT_TEMP", "0")
    assert sampling()["temperature"] == 0.0


def test_menu_always_passes_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(menuopts, "glossary_dir", lambda: tmp_path / "none")
    assert menuopts._glossary_args(lambda m: None) == ["--builtin-glossary"]
    (tmp_path / "u").mkdir()
    (tmp_path / "u" / "a.txt").write_text("x = y")
    monkeypatch.setattr(menuopts, "glossary_dir", lambda: tmp_path / "u")
    assert menuopts._glossary_args(lambda m: None) == ["--glossary", str(tmp_path / "u"), "--builtin-glossary"]


def test_builtin_terms_are_not_whisper_hotwords():
    """Built-in tech terms only steer MT; Whisper keeps the user's hotwords (and gets none from the built-in list)."""
    assert glossary.load_builtin().hotwords() is None
    user = glossary.Glossary(glossary.parse("роутер\nчастота = 頻率"))
    assert glossary.merge(user, glossary.load_builtin()).hotwords() == "роутер, частота"
    assert glossary.merge(None, glossary.load_builtin()).hotwords() is None


def test_everyday_sentence_gets_no_terminology_block(tmp_path):
    """The menu loads the built-in list for every talk: an everyday sentence must keep the plain prompt."""
    tr, llm = make(tmp_path, glossary.load_builtin())
    s = "The library terminal had an image of a tree branch and a log."
    tr.translate(s, "en", "zh")
    assert llm.calls[-1][0] == build_prompt(s, "en", "zh")
