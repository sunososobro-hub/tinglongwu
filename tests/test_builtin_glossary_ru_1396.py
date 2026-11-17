"""#1396: built-in Russian software slang list: size, ru-only, user entry wins, reaches the MT prompt."""
from tinglongwu import glossary
from tinglongwu.hymt import HyMtTranslator, hymt_path

RU_EXAMPLES = {"коммит": "提交", "прод": "正式環境", "релиз": "發佈", "баг": "程式錯誤", "фича": "功能",
               "ветка": "分支", "залить": "上傳", "железо": "硬體"}


def _ru_entries():
    return glossary.parse(glossary.BUILTIN_RU_PATH.read_text(encoding="utf-8"))


def test_ru_list_has_30_to_50_terms_and_the_issue_examples():
    n = sum(len(e.sources) for e in _ru_entries())
    assert 30 <= len(_ru_entries()) <= 50 and n >= 30  # entries (spellings count extra)
    by_main = {e.main: e.target for e in _ru_entries()}
    for k, v in RU_EXAMPLES.items():
        assert by_main[k] == v


def test_applied_only_for_russian_source():
    ru = {e.main for e in glossary.load_builtin("ru").entries}
    assert "коммит" in ru and "daemon" in ru
    assert "коммит" in {e.main for e in glossary.load_builtin("auto").entries}
    for lang in ("en", "ja", "de"):
        assert "коммит" not in {e.main for e in glossary.load_builtin(lang).entries}
    assert all(not e.asr_hint for e in glossary.load_builtin("ru").entries)


def test_user_entry_wins_over_ru_builtin():
    user = glossary.Glossary(glossary.parse("прод = 生產環境"))
    m = glossary.merge(user, glossary.load_builtin("ru"))
    assert [e.target for e in m.entries if "прод" in e.sources] == ["生產環境"]


def test_ru_term_reaches_prompt(tmp_path):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    calls = []

    class Llm:
        def create_chat_completion(self, messages, **kw):
            calls.append(messages[0]["content"])
            return {"choices": [{"message": {"content": "好"}}]}

    tr = HyMtTranslator(tmp_path, "tw", "cpu", loader=lambda p, g: Llm())
    tr.glossary = glossary.load_builtin("ru")
    tr.translate("Мы залили фичу в прод вчера.", "ru", "zh")
    assert all(f"{w}" in calls[-1] for w in ("上傳", "功能", "正式環境"))


def test_short_ru_terms_do_not_match_longer_words():
    g = glossary.load_builtin("ru")
    hit = lambda text: {e.main for e in g.relevant(text)}  # noqa: E731
    assert "прод" not in hit("Продолжение следует.")
    assert not {"лог", "логи"} & hit("Это логика программы.")
    assert "прод" in hit("Выкатили в проде.") and "баг" in hit("Нашли багов много.")
    assert "логи" in hit("Посмотри логи.") and "ветка" in hit("Создал ветку.")
