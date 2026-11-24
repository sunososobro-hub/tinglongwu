"""#1689: built-in multilingual term files (data/terms/<domain>.tsv): format check, any language pair, domain and
scenario filter, Russian case forms, CJK substring sources, target-only spellings, new files found without code."""
import itertools

import terms_check
from tinglongwu import glossary
from tinglongwu.glossary import TERM_LANGS, TERMS_DIR, load_builtin_pair, scenario_domains

HEAD = "# scenario: test\nid\ten\tru\tja\tko\tzh-TW\tzh-CN\n"


def test_shipped_term_files_pass_the_checker():
    files = sorted(TERMS_DIR.glob("*.tsv"))
    assert files and terms_check.check_files(files) == []
    assert terms_check.main([]) == 0
    assert sum(len(t.rows) for t in glossary.term_tables()) >= 60
    legacy = next(t for t in glossary.term_tables() if t.domain == "legacy-tech")
    for lang in TERM_LANGS:  # the migrated concepts are filled in every shipped language
        assert all(r[lang] for r in legacy.rows), lang


def test_checker_reports_bad_files(tmp_path):
    bad = {
        "no_scen.tsv": "id\ten\tru\tja\tko\tzh-TW\tzh-CN\nx\ta\tб\t\t\t\t\n",
        "scen_not_first.tsv": "# hi\n" + HEAD,
        "bad_head.tsv": "# scenario: t\nid\ten\tru\nx\ta\tб\n",
        "dup.tsv": HEAD + "a\tcompiler | Compiler\tкомпилятор\t\t\t\t\n",
        "dup_rows.tsv": HEAD + "a\tcompiler\t\t\t\t\t\nb\tcompiler\t\t\t\t\t\n",
        "dup_id.tsv": HEAD + "a\tcompiler\t\t\t\t\t\na\tlinker\t\t\t\t\t\n",
        "bad_id.tsv": HEAD + "Net.A\tcompiler\t\t\t\t\t\n",
        "empty_row.tsv": HEAD + "a\t\t\t\t\t\t\n",
        "cells.tsv": HEAD + "a\tx | \tб*в\t\t\t\t\n",
        "short_row.tsv": HEAD + "a\tx\tб\n",
        "Bad Name.tsv": HEAD + "a\tkernel\t\t\t\t核心程式\t\n",
    }
    for name, text in bad.items():
        assert terms_check.check_text(text, name), name
    ok = HEAD + "a.b\tkernel | *core\t\t\t\t核\t\n"
    assert terms_check.check_text(ok, "ok.tsv") == []
    extra = "# scenario: tech, *\nid\ten\tru\tja\tko\tzh-TW\tzh-CN\tvi\na\tkernel\t\t\t\t\t\tnhân\n"
    assert terms_check.check_text(extra, "extra.tsv") == []  # a new language column needs no code change
    assert terms_check.check_files([]) == []
    ids = {}
    assert terms_check.check_text(ok, "one.tsv", ids) == [] and terms_check.check_text(ok, "two.tsv", ids)
    p = tmp_path / "dup.tsv"
    p.write_text(bad["dup.tsv"], encoding="utf-8")
    assert terms_check.main([str(p)]) == 1


def test_every_language_pair_gets_entries():
    doms = [t.domain for t in glossary.term_tables()]
    for src, tgt in itertools.permutations(TERM_LANGS, 2):
        g = load_builtin_pair(src, tgt, doms)
        assert g.entries, (src, tgt)
        assert all(not e.asr_hint for e in g.entries)
    assert load_builtin_pair("ru", "xx", doms).entries == []
    assert load_builtin_pair("ru", "ru", doms).entries == []


def test_domain_and_scenario_filter():
    assert load_builtin_pair("en", "zh", []).entries == []
    assert load_builtin_pair("en", "zh", ["nope"]).entries == []
    assert "legacy-tech" in scenario_domains("tech")
    assert scenario_domains("") == [] and scenario_domains("cooking") == []  # default: nothing on yet


def test_zh_variant_picks_the_column():
    tw = {e.main: e.target for e in load_builtin_pair("en", "zh", ["legacy-tech"], "tw").entries}
    cn = {e.main: e.target for e in load_builtin_pair("en", "zh", ["legacy-tech"], "cn").entries}
    assert tw["compiler"] == "編譯器" and cn["compiler"] == "编译器"
    assert {e.main: e.target for e in load_builtin_pair("en", "zh-CN", ["legacy-tech"]).entries}["compiler"] == "编译器"


def test_ru_source_keeps_case_form_rules():
    g = load_builtin_pair("ru", "en", ["legacy-tech"])
    hit = lambda text: {e.main for e in g.relevant(text)}  # noqa: E731
    assert "прошивка" in hit("Нет свежих прошивок.") and "фича" in hit("Работаем над фичами.")
    for text in ("Мы поехали по железной дороге.", "Это тесто для пиццы.", "Продолжение следует."):
        assert g.relevant(text) == [], text


def test_cjk_sources_match_as_substrings_and_target_only_is_never_a_source():
    zh = load_builtin_pair("zh-TW", "en", ["legacy-tech"])
    assert "deployment" in {e.target for e in zh.relevant("我們明天要部署新版本")}
    assert zh.relevant("我明天要提交報告，再合併兩份文件") == []  # 提交 / 合併 are target-only (*)
    ja = load_builtin_pair("ja", "en", ["legacy-tech"])
    assert "compiler" in {e.target for e in ja.relevant("新しいコンパイラでビルドした")}
    en = load_builtin_pair("en", "zh", ["legacy-tech"])
    assert {e.target for e in en.relevant("We commit before the release.")} >= {"提交", "發佈"}
    assert en.relevant("This feature film has a great server.") == []  # en feature / server are target-only


def test_new_domain_file_is_found_without_code(tmp_path):
    (tmp_path / "audio.tsv").write_text("# scenario: music, tech\nid\ten\tru\tja\tko\tzh-TW\tzh-CN\tvi\n"
                                        "au.codec\tcodec\tкодек\tコーデック\t코덱\t編解碼器\t编解码器\tbộ mã hóa\n",
                                        encoding="utf-8")
    assert scenario_domains("music", tmp_path) == ["audio"]
    g = load_builtin_pair("ru", "zh", scenario_domains("tech", tmp_path), folder=tmp_path)
    assert [(e.main, e.target) for e in g.relevant("Новый кодек.")] == [("кодек", "編解碼器")]
    vi = load_builtin_pair("vi", "en", ["audio"], folder=tmp_path)  # a column the code does not list
    assert [e.target for e in vi.relevant("bộ mã hóa mới")] == ["codec"]
    assert load_builtin_pair("auto", "vi", ["audio"], folder=tmp_path).entries


def test_user_entries_win_over_term_table():
    user = glossary.Glossary(glossary.parse("compiler = 編譯程式"))
    m = glossary.merge(user, load_builtin_pair("en", "zh", ["legacy-tech"]))
    assert [e.target for e in m.entries if "compiler" in e.sources] == ["編譯程式"]


def _builtin_terms(user, lang, to, face_to_face=None):
    from types import SimpleNamespace

    from tinglongwu import cli

    a = SimpleNamespace(lang=lang, to=to, face_to_face=face_to_face, term_scenario="tech", zh_variant=None)
    return cli.merge_builtin_terms(user, a)


def _targets(g, src):
    return [e.target for e in g.entries if any(s.lower() == src for s in e.sources)]


def test_en_to_ru_pair_terms_beat_the_chinese_builtin_list():
    """Opus CHANGES on PR #1695: tech-dev.txt (en -> zh) was merged before the pair table, so en -> ru got Chinese
    targets for every shared term. Pair terms now come first; user entries still win over both."""
    g = _builtin_terms(None, "en", "ru")
    for src in ("driver", "kernel", "daemon"):
        tg = _targets(g, src)
        assert tg and all(t and not any("一" <= c <= "鿿" for c in t) for t in tg), (src, tg)
    assert _targets(g, "driver")[0] == "драйвер"
    user = glossary.Glossary(glossary.parse("driver = мой драйвер"))
    assert _targets(_builtin_terms(user, "en", "ru"), "driver") == ["мой драйвер"]


def test_en_to_zh_keeps_chinese_targets_and_face_to_face_keeps_the_builtin_list():
    zh = _targets(_builtin_terms(None, "en", "zh"), "driver")
    assert zh and all(any("一" <= c <= "鿿" for c in t) for t in zh)
    assert _targets(_builtin_terms(None, "en", "zh", face_to_face="en"), "driver") == ["驅動程式"]


def test_cjk_substring_source_with_a_middle_dot_matches_its_own_spelling():
    e = glossary.Entry(["ニューラル・ネットワーク"], "神經網路", substr=True)
    g = glossary.Glossary([e])
    assert g.relevant("この ニューラル・ネットワーク は") == [e]
    assert glossary.Entry(["・"], "x", substr=True).matches(["a"]) is False  # punctuation-only source never matches


def test_ru_source_uses_tech_domains_by_default_1799():
    """#1799: a default ru session loads the tech domain tables; --term-scenario none (or another source) does not."""
    from types import SimpleNamespace

    from tinglongwu.cli import merge_builtin_terms

    def terms(lang, scenario):
        a = SimpleNamespace(lang=lang, to="zh", zh_variant=None, face_to_face=False, term_scenario=scenario)
        return {e.main for e in merge_builtin_terms(None, a).entries}

    default, off = terms("ru", ""), terms("ru", "none")
    assert terms("ru", "tech") == default and len(default - off) > 1000
    assert len(terms("en", "")) < len(default)
    assert glossary.effective_scenario("", "ru") == "tech" and glossary.effective_scenario("none", "ru") == ""
    assert glossary.effective_scenario("", "en") == "" and glossary.effective_scenario("security", "ru") == "security"
