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
    tr.translate("Hello there my friend.", "en", "zh")
    assert llm.calls[-1][0] == build_prompt("Hello there my friend.", "en", "zh")
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
