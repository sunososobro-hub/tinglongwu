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
