"""#981: (mode, My language, Other side language) -> source / target / capture, from one function only."""
import pytest

from tinglongwu.gui import core
from tinglongwu.langs import SOURCE_LANGS

MINE = list(core.TARGETS)  # the "My language" choices
OTHER = [c for c in SOURCE_LANGS if c != "auto"]  # the "Other side language" choices
PAIRS = [(m, o) for m in MINE for o in OTHER if core.lang_base(m) != core.lang_base(o)]


def _speak_target(other):
    return "zh-tw" if other == "zh" else other


@pytest.mark.parametrize("mine,other", PAIRS)
def test_lang_pair_listen_is_other_to_mine_and_speak_is_mine_to_other(mine, other):
    (lis,) = core.session_route("listen", mine, other)
    (spk,) = core.session_route("speak", mine, other)
    assert (lis["lang"], lis["target"]) == (other, mine)
    assert (spk["lang"], spk["target"]) == (core.lang_base(mine), _speak_target(other))
    for leg in (lis, spk):
        assert core.lang_base(leg["lang"]) != core.lang_base(leg["target"])  # src == dst never occurs


@pytest.mark.parametrize("mine,other", PAIRS)
def test_lang_pair_two_way_runs_both_legs(mine, other):
    legs = core.session_route("two-way", mine, other)
    assert [(g["leg"], g["source"]) for g in legs] == [("listen", "system"), ("speak", "mic")]
    assert legs[0]["lang"] == other and legs[1]["target"] == _speak_target(other)


@pytest.mark.parametrize("mine,other", [("zh-tw", "zh"), ("zh-cn", "zh"), ("en", "en"), ("zh", "zh")])
@pytest.mark.parametrize("mode", core.SESSION_MODES)
def test_lang_pair_same_language_is_refused(mode, mine, other):
    with pytest.raises(core.SameLanguageError):
        core.session_route(mode, mine, other)


@pytest.mark.parametrize("mine,other", [("zh-cn", "zh"), ("en", "en")])
@pytest.mark.parametrize("mode", core.QUICK_MODES)
def test_lang_pair_quick_settings_refuses_same_language(mode, mine, other):
    with pytest.raises(core.SameLanguageError) as e:
        core.quick_settings(dict(core.DEFAULTS, target=mine, lang=other), mode)
    from tinglongwu.i18n import t

    assert t(e.value.key) != e.value.key  # a real message, not the raw key


@pytest.mark.parametrize("saved_source", ["system", "mic", "", None, "loopback"])
@pytest.mark.parametrize("mine,other", [("zh-tw", "en"), ("en", "ja"), ("zh-cn", "auto")])
def test_speak_source_is_always_the_default_microphone(saved_source, mine, other):
    run = core.quick_settings(dict(core.DEFAULTS, source=saved_source, target=mine, lang=other), "speak")
    assert run["source"] == "mic"
    argv = core.settings_to_argv(run)
    i = argv.index("--mic")
    assert i == len(argv) - 1 or argv[i + 1].startswith("--")  # no device name: the system default microphone


@pytest.mark.parametrize("saved_source", ["system", "mic"])
def test_listen_source_is_the_pc_sound(saved_source):
    run = core.quick_settings(dict(core.DEFAULTS, source=saved_source, target="zh-tw", lang="en"), "listen")
    assert run["source"] == "system" and "--mic" not in core.settings_to_argv(run)


def test_speak_argv_follows_the_pair_owner_meet_case():
    """Owner Meet test 2026-11-15: My language zh, Other side en -> speak runs zh -> en (never en -> en)."""
    from tinglongwu import cli

    run = core.quick_settings(dict(core.DEFAULTS, target="zh-tw", lang="en", source="system"), "speak")
    a = cli.parse_args(core.settings_to_argv(run))
    assert (a.lang, a.to, a.mic) == ("zh", "en", True)


def test_unknown_session_mode_is_rejected():
    with pytest.raises(ValueError):
        core.session_route("sing", "zh-tw", "en")
