"""#1256: one person must not be split into many labels after short pauses (noisy short-sentence embeddings)."""
import math

from tinglongwu import speakers
from tinglongwu.speakers import SpeakerClusterer, cosine

SHORT_SECONDS = getattr(speakers, "SHORT_SECONDS", 3.0)
SHORT_THRESHOLD = getattr(speakers, "SHORT_THRESHOLD", 0.5)
LONG = SHORT_SECONDS + 2.0
SHORT = (1.0 + SHORT_SECONDS) / 2


def _at(cos: float) -> list[float]:
    """A unit vector whose cosine with [1, 0, 0] is `cos`."""
    return [cos, math.sqrt(1 - cos * cos), 0.0]


def test_short_noisy_sentence_keeps_the_speaker():
    mid = (SHORT_THRESHOLD + 0.6) / 2  # below the long threshold, above the short one
    c = SpeakerClusterer()
    assert c.assign([1, 0, 0], LONG) == "說話者1"
    assert c.assign(_at(mid), SHORT) == "說話者1"  # before #1256: a new label 說話者2
    assert c.count == 1


def test_long_sentence_still_opens_a_new_speaker():
    c = SpeakerClusterer()
    c.assign([1, 0, 0], LONG)
    assert c.assign(_at((SHORT_THRESHOLD + 0.6) / 2), LONG) == "說話者2"
    assert c.count == 2


def test_short_sentence_far_from_everyone_is_a_new_speaker():
    c = SpeakerClusterer()
    c.assign([1, 0, 0], LONG)
    assert c.assign([0, 1, 0], SHORT) == "說話者2"


def test_split_clusters_that_converge_are_merged_into_the_older_label():
    c = SpeakerClusterer()
    assert c.assign([1, 0, 0], LONG) == "說話者1"
    assert c.assign(_at(0.45), LONG) == "說話者2"  # one person, first heard far from their usual voice
    labels = [c.assign(_at(0.8), LONG) for _ in range(6)]  # the same person again, closer to cluster 1
    assert labels[-1] == "說話者1"
    assert c.count == 1
    assert c.merged == {"說話者2": "說話者1"}


def test_different_speakers_are_never_merged():
    c = SpeakerClusterer()
    for _ in range(10):
        c.assign([1, 0, 0], LONG)
        c.assign([0, 1, 0], LONG)
        c.assign([0, 0, 1], SHORT)
    assert c.count == 3
    assert c.merged == {}
    assert cosine([1, 0, 0], [0, 1, 0]) == 0.0


def test_kept_maps_a_merged_label_and_leaves_others():
    c = SpeakerClusterer()
    c.assign([1, 0, 0], LONG)
    c.assign(_at(0.45), LONG)
    for _ in range(6):
        c.assign(_at(0.8), LONG)
    assert c.kept("說話者2") == "說話者1"
    assert c.kept("說話者1") == "說話者1"
    assert c.kept(None) is None
    assert c.merges() == {"說話者2": "說話者1"}
    assert c.merge_edges() == [("說話者2", "說話者1")]


def test_gui_shows_the_kept_label_on_lines_labelled_before_the_merge():
    from tinglongwu.gui import app as appmod
    from tinglongwu.gui.core import SpeakerColors
    from tinglongwu.speakers import SpeakerBook

    class _Spk:
        def result(self, tok):
            return {"spk#1": "說話者1", "spk#2": "說話者2"}.get(tok)

    class _State:
        def history_who(self):
            return ["spk#1", "spk#2"]

    cls = [v for v in vars(appmod).values() if isinstance(v, type) and hasattr(v, "speaker_prefix")
           and hasattr(v, "_rename_speaker")][0]
    o = object.__new__(cls)
    o.spk, o.state, o.spk_book, o.spk_colors = _Spk(), _State(), SpeakerBook(), SpeakerColors()
    o.spk_clusterer = SpeakerClusterer()
    assert o._speaker_label(1)[0] == "說話者2"  # before the merge: its own label
    o.spk_clusterer.merged["說話者2"] = "說話者1"
    o.spk_clusterer._edges.append(("說話者2", "說話者1"))
    assert o._speaker_label(1)[0] == "說話者1"  # the earlier line now shows the kept speaker
    assert o.speaker_prefix(0) == o.speaker_prefix(1)


def test_gui_keeps_a_name_given_to_the_retired_label():
    from tinglongwu.gui import app as appmod
    from tinglongwu.gui.core import SpeakerColors
    from tinglongwu.speakers import SpeakerBook

    class _Spk:
        def result(self, tok):
            return {"spk#1": "說話者1", "spk#2": "說話者2"}.get(tok)

    class _State:
        def history_who(self):
            return ["spk#1", "spk#2"]

    cls = [v for v in vars(appmod).values() if isinstance(v, type) and hasattr(v, "speaker_prefix")
           and hasattr(v, "_rename_speaker")][0]
    o = object.__new__(cls)
    o.spk, o.state, o.spk_book, o.spk_colors = _Spk(), _State(), SpeakerBook(), SpeakerColors()
    o.spk_clusterer = SpeakerClusterer()
    o.spk_book.rename("說話者2", "Alice")  # named before the merge
    o.spk_clusterer.merged["說話者2"] = "說話者1"
    o.spk_clusterer._edges.append(("說話者2", "說話者1"))
    assert o._speaker_label(0)[1] == "Alice" and o._speaker_label(1)[1] == "Alice"


def test_chained_merges_replayed_in_order_keep_the_name_on_the_middle_label():
    """3 -> 2, then 2 -> 1 in one merge pass: replaying the edges in order (not the flattened map) keeps Alice."""
    from tinglongwu.speakers import SpeakerBook

    c = SpeakerClusterer()
    c._sums = [[1.0, 0.0, 0.0], [4.5, 2.18, 0.0], [0.6, 0.8, 0.0]]  # 3 is near 2 (0.89), not 1 (0.6); 2 has 5 lines
    c.merge_threshold = 0.75
    assert c._merge_near(2) == 0  # 3 joins 2, then the grown 2 joins 1
    assert c.merge_edges() == [("說話者3", "說話者2"), ("說話者2", "說話者1")]
    book = SpeakerBook()
    book.rename("說話者3", "Bob")
    book.rename("說話者2", "Alice")
    for old, kept in c.merge_edges():
        book.merge(old, kept)
    assert book.display("說話者1") == book.display("說話者3") == "Alice"
