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


def test_ru_terms_do_not_fire_on_other_words_with_the_same_stem_1425():
    g = glossary.load_builtin("ru")
    for s in ("Мы поехали по железной дороге.", "Железнодорожный вокзал рядом.", "Это тесто для пиццы."):
        assert g.relevant(s) == [], s
    for s in ("У нас новое железо.", "на этом железе"):
        assert [e.main for e in g.relevant(s)] == ["железо"], s
    for s in ("тесты упали", "в тестах"):
        assert [e.main for e in g.relevant(s)] == ["тесты"], s


def test_ru_noun_forms_still_fire_1425():
    g = glossary.load_builtin("ru")
    hit = lambda text: {e.main for e in g.relevant(text)}  # noqa: E731
    for text, term in (("Это деплой.", "деплой"), ("после деплоя", "деплой"), ("нужно ревью", "ревью"),
                       ("в ветках", "ветка"), ("новыми ветками", "ветка"), ("в репозитории", "репозиторий"),
                       ("об отладке", "отладка"), ("отладками", "отладка")):
        assert term in hit(text), (text, term)


def test_ru_verbs_fire_on_past_tense_1429():
    g = glossary.load_builtin("ru")
    cases = {"Я задеплоил сервис.": "задеплоить", "Мы задеплоили вчера.": "задеплоить", "Пофиксил всё.": "пофиксить",
             "Смержил всё.": "смержить", "Откатил всё.": "откатить"}
    for sent, main in cases.items():
        assert main in [e.main for e in g.relevant(sent)], sent


def test_ru_verbs_keep_present_and_reflexive_matches_1429():
    g = glossary.load_builtin("ru")
    for sent, main in {"Он задеплоит сервис.": "задеплоить", "Я пофиксит всё.": "пофиксить",
                       "Надо смержить.": "смержить", "Нужно откатить.": "откатить"}.items():
        assert main in [e.main for e in g.relevant(sent)], sent


def test_ru_feminine_plural_and_fleeting_vowel_forms_fire_1428():
    g = glossary.load_builtin("ru")
    hit = lambda text: {e.main for e in g.relevant(text)}  # noqa: E731
    for text in ("Работаем над фичами.", "Это в фичах.", "Сказал фичам.", "Новая фича.", "Нет этой фичи."):
        assert "фича" in hit(text), text
    assert "прошивка" in hit("Нет свежих прошивок.")
    assert "ветка" in hit("Много веток.") and "отладка" in hit("Без отладок.")
    for text in ("Это фикус.", "Фичеринг альбома."):
        assert "фича" not in hit(text), text


def test_ru_verbs_fire_on_future_and_imperative_1432():
    g = glossary.load_builtin("ru")
    cases = {"Задеплоим завтра.": "задеплоить", "Задеплоят завтра.": "задеплоить", "Задеплой сервис.": "задеплоить",
             "Пофиксим к релизу.": "пофиксить", "Пофикси это.": "пофиксить", "Откатим назад.": "откатить",
             "Смержим вечером.": "смержить"}
    for sent, main in cases.items():
        assert main in [e.main for e in g.relevant(sent)], sent


def test_ru_verbs_fire_on_1sg_and_2sg_1434():
    g = glossary.load_builtin("ru")
    cases = {"Я задеплою сервис.": "задеплоить", "Ты задеплоишь сервис?": "задеплоить",
             "Я пофикшу баг.": "пофиксить", "Пофиксишь?": "пофиксить", "Смержу ветку.": "смержить",
             "Откачу релиз.": "откатить", "Откатишь?": "откатить"}
    for text, verb in cases.items():
        assert verb in {e.main for e in g.relevant(text)}, text
