"""#1917: the UI text moved from one 1,300-line i18n.py into i18n_<topic>.py modules merged by tinglongwu.i18n."""
import pytest

from tinglongwu import i18n


def test_every_topic_has_every_language_with_the_same_keys():
    for mod in i18n.TOPICS:
        assert tuple(mod.MESSAGES) == i18n.LANGS, mod.__name__
        en = list(mod.MESSAGES["en"])
        assert en, mod.__name__
        for lang in i18n.LANGS:
            assert list(mod.MESSAGES[lang]) == en, (mod.__name__, lang)


def test_no_key_is_defined_in_two_topics():
    seen: dict[str, str] = {}
    for mod in i18n.TOPICS:
        for key in mod.MESSAGES["en"]:
            assert key not in seen, (key, seen.get(key), mod.__name__)
            seen[key] = mod.__name__


def test_merged_table_is_exactly_the_topics():
    for lang in i18n.LANGS:
        merged = {}
        for mod in i18n.TOPICS:
            merged.update(mod.MESSAGES[lang])
        # fmt.py adds label_me / label_them to the merged table at import (not part of any topic)
        table = {k: v for k, v in i18n.MESSAGES[lang].items() if k not in ("label_me", "label_them")}
        assert table == merged, lang
    assert set(i18n.MESSAGES) == set(i18n.LANGS)


@pytest.mark.parametrize("lang", ["en", "ru", "zh-TW", "ja"])
def test_t_reads_each_topic_in_each_language(monkeypatch, lang):
    monkeypatch.setattr(i18n, "_override", lang)
    for mod in i18n.TOPICS:
        key = next(iter(mod.MESSAGES[lang]))
        text = mod.MESSAGES[lang][key]
        if "{" not in text:
            assert i18n.t(key) == text, (mod.__name__, key)


def test_fmt_labels_still_land_in_the_merged_table():
    from tinglongwu import fmt  # noqa: F401  (updates i18n.MESSAGES at import)

    assert i18n.MESSAGES["en"]["label_me"] == "▶ Me"
    assert i18n.MESSAGES["ja"]["label_them"] == "◀ 相手"
