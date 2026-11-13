"""#390: per-language pre/post-MT hooks (tinglongwu/lang/<code>/) and the first patch, Russian name declension."""
import re
from pathlib import Path

from tinglongwu import lang as lang_patches
from tinglongwu.lang import ru
from tinglongwu.translate import Translation, Translator, translate_checked

# 20 RU sentences with declined person names -> the surname as MT should see it (nominative, real gender).
# 12 male (the live failure "Максима Чижова" -> 奇佐娃 is the first), 8 female (incl. nominative and oblique cases).
CASES = [
    ("Спасибо, Максима Чижова мы ждём.", "Чижов", "m"),
    ("Мы пригласили Максима Чижова.", "Чижов", "m"),
    ("Я передал документы Максиму Чижову.", "Чижов", "m"),
    ("Отчёт подготовлен Максимом Чижовым.", "Чижов", "m"),
    ("Мы говорили о Максиме Чижове.", "Чижов", "m"),
    ("Позвоните Ивану Петрову.", "Петров", "m"),
    ("Встреча с Иваном Петровым завтра.", "Петров", "m"),
    ("Дом Ивана Петрова стоит у реки.", "Петров", "m"),
    ("Слово предоставляется Сергею Кузнецову.", "Кузнецов", "m"),
    ("Без Сергея Кузнецова мы не начнём.", "Кузнецов", "m"),
    ("Звонили Дмитрию Соколову.", "Соколов", "m"),
    ("Книга Льва Толстого лежит на столе.", "Толстой", "m"),
    ("Анна Иванова откроет совещание.", "Иванова", "f"),
    ("Мы ждём Анну Иванову.", "Иванова", "f"),
    ("Передайте Анне Ивановой привет.", "Иванова", "f"),
    ("Отчёт подготовлен Анной Ивановой.", "Иванова", "f"),
    ("Ольга Смирнова уже здесь.", "Смирнова", "f"),
    ("Без Ольги Смирновой мы не начнём.", "Смирнова", "f"),
    ("Это сказала Мария Кузнецова.", "Кузнецова", "f"),
    ("Он работает с Еленой Сергеевной Орловой.", "Орлова", "f"),
]


def _errors(transform, cases=CASES):
    """Sentences whose MT input does not contain the surname in its nominative form of the right gender."""
    return [s for s, sur, _g in cases if not re.search(rf"(?<!\w){sur}(?!\w)", transform(s))]


def test_name_error_rate_before_vs_after():
    before, after = _errors(lambda s: s), _errors(ru.pre_mt)
    print(f"\n#390 RU name gender/lemma errors: before {len(before)}/20, after {len(after)}/20")
    assert len(before) == 17 and after == []  # before: every oblique case; the 3 nominative females are already right
    female = [c for c in CASES if c[2] == "f"]
    assert _errors(ru.pre_mt, female) == []  # no regression on female surnames
    assert not any(re.search(r"(?<!\w)(Иванов|Смирнов|Кузнецов|Орлов)(?!\w)", ru.pre_mt(s)) for s, _, _ in female)


def test_live_example_and_ambiguous_lone_surname_kept():
    assert ru.pre_mt("Максима Чижова.") == "Максим Чижов."
    assert ru.pre_mt("Иванова сказала, что придёт.") == "Иванова сказала, что придёт."  # male gen. or female nom.
    assert ru.pre_mt("Передайте это Ивановой.") == "Передайте это Иванова."  # only female
    assert ru.pre_mt("Мы купили новый дом, Роза цветёт.") == "Мы купили новый дом, Роза цветёт."


class _Echo(Translator):
    def __init__(self):
        self.seen = []

    def translate(self, text, src, tgt):
        self.seen.append((text, src))
        return Translation(None, f"<{text}>")


def test_translate_checked_runs_the_source_language_hook():
    tr = _Echo()
    assert translate_checked(tr, "Мы пригласили Максима Чижова.", "ru", "zh").text == "<Мы пригласили Максим Чижов.>"
    translate_checked(tr, "Максима Чижова", "en", "zh")  # hook is keyed by the source language only
    assert tr.seen[-1] == ("Максима Чижова", "en")


def test_post_mt_hook_by_target_language(monkeypatch):
    monkeypatch.setattr(ru, "post_mt", lambda text, src: text.upper() + f"/{src}", raising=False)
    assert translate_checked(_Echo(), "hi", "en", "ru").text == "<HI>/en"
    assert translate_checked(_Echo(), "hi", "en", "de").text == "<hi>"


def test_without_pymorphy_the_text_passes_through(monkeypatch):
    monkeypatch.setattr(ru, "_morph", None)
    monkeypatch.setattr(ru, "_unavailable", True)
    assert ru.pre_mt("Мы пригласили Максима Чижова.") == "Мы пригласили Максима Чижова."


def test_core_has_no_russian_branch_for_this_feature():
    core = Path(lang_patches.__file__).resolve().parent.parent
    for name in ("translate.py", "pipeline.py", "stream.py", "hymt.py"):
        text = (core / name).read_text("utf-8")
        assert not re.search(r"""(src|lang)\s*==\s*["']ru["']""", text), name


def test_exe_build_bundles_the_dictionary_and_smoke_checks_it():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import build_windows
    from tlwu_app import smoke

    cmd = build_windows.nuitka_cmd()
    for flag in ("--include-package=pymorphy3", "--include-package=pymorphy3_dicts_ru",
                 "--include-package-data=pymorphy3_dicts_ru", "--include-package=dawg_python"):
        assert flag in cmd
    assert ("tinglongwu.lang.ru", True) in smoke.MODULES
    smoke.check_ru_names()  # real dictionary loaded by path: passes


def test_smoke_fails_when_the_dictionary_is_missing():
    import io
    from tlwu_app import smoke

    def missing():
        raise FileNotFoundError("pymorphy3_dicts_ru/data/meta.json")

    out = io.StringIO()
    rc = smoke.check(modules=[("tinglongwu.lang.ru", True)], out=out, lazy=[])
    assert rc == 0 and "OK   tinglongwu.lang.ru" in out.getvalue()
    orig = smoke.IMPORTERS["tinglongwu.lang.ru"]
    try:
        smoke.IMPORTERS["tinglongwu.lang.ru"] = lambda: smoke.check_ru_names(load=missing)
        out = io.StringIO()
        assert smoke.check(modules=[("tinglongwu.lang.ru", True)], out=out, lazy=[]) == 1
        assert "FAIL tinglongwu.lang.ru: FileNotFoundError" in out.getvalue()
    finally:
        smoke.IMPORTERS["tinglongwu.lang.ru"] = orig


def test_two_people_keep_the_object_case_so_roles_stay_distinct():
    a = ru.pre_mt("Анна Иванова любит Ивана Петрова.")  # Anna loves Ivan
    b = ru.pre_mt("Анну Иванову любит Иван Петров.")  # Ivan loves Anna (object first)
    assert a == "Анна Иванова любит Ивана Петрова."
    assert b == "Анну Иванову любит Иван Петров."
    assert a != b
    # two people, but neither name can be an accusative object: still normalized
    assert ru.pre_mt("Максим Чижов говорил с Анной Ивановой.") == "Максим Чижов говорил с Анна Иванова."


def test_same_language_and_hook_errors_leave_the_text_alone(monkeypatch):
    tr = _Echo()
    assert translate_checked(tr, "Максима Чижова", "ru", "ru").text == "<Максима Чижова>"

    def boom(text, morph=None):
        raise ValueError("odd token")

    monkeypatch.setattr(ru, "normalize_names", boom)
    assert ru.pre_mt("Максима Чижова.") == "Максима Чижова."


def test_exe_notices_state_the_dictionary_data_license(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import build_windows

    build_windows.write_licenses(tmp_path, None)
    notices = (tmp_path / "licenses" / "THIRD_PARTY_NOTICES.txt").read_text("utf-8")
    line = next(ln for ln in notices.splitlines() if ln.lower().startswith("pymorphy3-dicts-ru "))
    assert "CC BY-SA 3.0" in line and "OpenCorpora" in line
