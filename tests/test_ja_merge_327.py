"""#327: Japanese finals in listen mode (meeting preset, max_wait 6 s) must not sit for the whole max_wait when Whisper
leaves out the final 。, while en / ru keep their old merge behaviour."""
import re
from pathlib import Path

from tinglongwu import lang as lang_patches
from tinglongwu.langfollow import script_lang
from tinglongwu.merge import PAUSE_SEND, SentenceMerger, split_clause

ROOT = Path(__file__).resolve().parents[1]

MEETING_WAIT = 6.0
BUDGET = 2.5  # seconds a finished ja sentence may be held by the merger after its segment arrives (was 6.0)


def texts(units):
    return [u.text for u in units]


def _feed(m, segs):
    """segs: (text, lang, t, forced_cut, pause). Returns [(release time, text, forced)] incl. timer-driven due()."""
    out, t = [], 0.0
    for text, lang, t, forced, pause in segs:
        out += [(t, u.text, u.forced) for u in m.add(text, lang, t, forced_cut=forced, pause=pause)]
    while m.buf:  # the pipeline's timer: due() until the buffer is released
        t += 0.25
        out += [(t, u.text, u.forced) for u in m.due(t)]
    return out


def _held(segs, released):
    """Seconds between the last segment of each released unit arriving and its release."""
    return max(rt - max(s[2] for s in segs if s[2] <= rt) for rt, _, _ in released)


# VAD pause cuts as the segmenter reports them: 0.6-0.95 s (min_silence 0.6 + step / pad)
JA_NO_PERIOD = [("エアロスミスはツアーで予定していたコンサートを中止しました", "ja", 3.0, False, 0.62)]
JA_COMMA_END = [("技術決定論のほとんどの解釈は、", "ja", 3.0, False, 0.7)]
JA_TWO_SEGS = [("地殻が薄いため、近い方には海が多くなることがあります", "ja", 4.0, False, 0.75),
               ("溶岩が浮上しやすくなっていました", "ja", 7.5, False, 0.66)]
JA_ELLIPSIS_FORCED = [("都市はモロッコのスルタンによってダルル・バディアとして再建され…", "ja", 8.0, True, 0.0),
                      ("貿易拠点を設立したスペイン人貿易商によってカサブランカと名付けられました", "ja", 11.0, False, 0.7)]


# real large-v3-turbo output on FLEURS ja (eval run 2026-11-12, meeting preset): no 。 or 、 at all, and these three
# pause cuts were reported below PAUSE_SEND (0.8), so each was held the full 6 s before the fix
JA_EVAL = [[("エアロ・スミスはツアーで予定していたコンサートを中止しました", "ja", 2.0, False, 0.616)],
           [("しかしそうではありませんでした裏面に何か書かれていますが宝の地図ではありませんでした", "ja", 2.0, False, 0.764)],
           [("北はサヘルト教会を接して南と西は大西洋に囲まれた地域です", "ja", 2.0, False, 0.648)]]


def test_ja_without_final_period_released_at_pause_not_max_wait():
    for segs in (JA_NO_PERIOD, JA_COMMA_END, JA_TWO_SEGS, JA_ELLIPSIS_FORCED, *JA_EVAL):
        m = SentenceMerger(max_wait=MEETING_WAIT)
        rel = _feed(m, segs)
        assert rel and "".join(r[1] for r in rel).endswith(segs[-1][0][-6:])  # everything released
        assert _held(segs, rel) <= BUDGET, (segs, rel)
        assert _held(segs, rel) < MEETING_WAIT


def test_ja_single_unit_is_released_immediately_with_whole_text():
    m = SentenceMerger(max_wait=MEETING_WAIT)
    u = m.add("エアロスミスはツアーで予定していたコンサートを中止しました", "ja", 0, pause=0.62)
    assert texts(u) == ["エアロスミスはツアーで予定していたコンサートを中止しました"] and m.deadline is None
    m2 = SentenceMerger(max_wait=MEETING_WAIT)  # ends in 、 (the speaker stopped at a comma)
    assert texts(m2.add("技術決定論のほとんどの解釈は、", "ja", 0, pause=0.7)) == ["技術決定論のほとんどの解釈は、"]


def test_ja_forced_cut_still_waits_for_the_rest():
    m = SentenceMerger(max_wait=MEETING_WAIT)
    assert m.add("都市はモロッコのスルタンによって再建され…", "ja", 0, forced_cut=True, pause=0.0) == []
    u = m.add("カサブランカと名付けられました", "ja", 3.0, pause=0.7)
    assert texts(u) == ["都市はモロッコのスルタンによって再建されカサブランカと名付けられました"]


def test_ja_with_period_still_splits_at_sentence_end():
    m = SentenceMerger(max_wait=MEETING_WAIT)
    u = m.add("しかし、そうではありませんでした。裏面に何か書かれていますが", "ja", 0, forced_cut=True)
    assert texts(u) == ["しかし、そうではありませんでした。"] and m.buf == "裏面に何か書かれていますが"


def test_kana_labelled_zh_counts_as_japanese():
    # the pipeline hands the merger script_lang(text, lang): auto-detect said zh, kana says ja (#86)
    m = SentenceMerger(max_wait=MEETING_WAIT)
    text = "安全に泳ぐことができます"
    assert texts(m.add(text, script_lang(text, "zh"), 0, pause=0.6)) == [text]
    src = (ROOT / "tinglongwu" / "pipeline.py").read_text("utf-8")
    assert "merger.add(t.text, script_lang(t.text, t.lang)" in src


def test_core_merge_has_no_language_branch():
    # #390: the ja pause threshold lives in tinglongwu/lang/ja/, not as `lang == "ja"` in merge.py
    src = (ROOT / "tinglongwu" / "merge.py").read_text("utf-8")
    assert not re.search(r"""lang\s*(==|!=|in)\s*[\(\["']""", src)
    assert '"ja"' not in src and "'ja'" not in src
    assert lang_patches.setting("ja-JP", "MERGE_PAUSE_SEND", PAUSE_SEND) < PAUSE_SEND
    assert lang_patches.setting("en", "MERGE_PAUSE_SEND", PAUSE_SEND) == PAUSE_SEND


def test_ja_short_pause_below_vad_minimum_still_waits():
    m = SentenceMerger(max_wait=MEETING_WAIT)
    assert m.add("安全に泳ぐことが", "ja", 0, pause=0.3) == []


def test_en_ru_pause_threshold_unchanged():
    assert PAUSE_SEND == 0.8
    for text, lang in (("I went to the store and", "en"), ("я был в Австралии и", "ru")):
        m = SentenceMerger(max_wait=MEETING_WAIT)
        assert m.add(text, lang, 0, pause=0.7) == []  # below 0.8: keeps waiting, as before
        assert m.deadline == MEETING_WAIT
        assert m.due(MEETING_WAIT - 0.01) == [] and texts(m.due(MEETING_WAIT)) == [text]
        m2 = SentenceMerger(max_wait=MEETING_WAIT)
        assert texts(m2.add(text, lang, 0, pause=0.85)) == [text]
        m3 = SentenceMerger(max_wait=MEETING_WAIT)
        assert texts(m3.add("It works. And then", lang, 0, pause=0.7)) == ["It works."] and m3.buf == "And then"


def test_whole_mode_clause_cut_at_japanese_comma():
    assert split_clause("地殻が薄いため、近い方には海が多くなる") == ("地殻が薄いため、", "近い方には海が多くなる")
    m = SentenceMerger(max_wait=1.5, whole=True)
    m.add("地殻が薄いため、近い方には海が", "ja", 0, forced_cut=True)
    assert texts(m.due(1.5)) == ["地殻が薄いため、"] and m.buf == "近い方には海が"
