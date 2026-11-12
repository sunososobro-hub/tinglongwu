"""#221: online speaker clustering (pure logic)."""
import pytest

from tinglongwu.speakers import SpeakerClusterer, cosine


def test_same_vector_same_speaker():
    c = SpeakerClusterer()
    assert c.assign([1, 0, 0], 2.0) == "說話者1"
    assert c.assign([2, 0, 0], 2.0) == "說話者1"


def test_orthogonal_new_speaker_and_numbering_increments():
    c = SpeakerClusterer()
    assert c.assign([1, 0, 0], 2.0) == "說話者1"
    assert c.assign([0, 1, 0], 2.0) == "說話者2"
    assert c.assign([0, 0, 1], 2.0) == "說話者3"
    assert c.assign([0, 1, 0], 2.0) == "說話者2"
    assert c.count == 3


def test_threshold_boundary():
    v = [0.8, 0.6, 0.0]  # cosine with [1,0,0] == 0.8
    assert cosine(v, [1, 0, 0]) == pytest.approx(0.8)
    c = SpeakerClusterer(threshold=0.8)
    c.assign([1, 0, 0], 2.0)
    assert c.assign(v, 2.0) == "說話者1"  # == threshold -> same
    c = SpeakerClusterer(threshold=0.81)
    c.assign([1, 0, 0], 2.0)
    assert c.assign(v, 2.0) == "說話者2"  # just below -> new


def test_short_sentence_reuses_previous_speaker():
    c = SpeakerClusterer(min_seconds=1.0)
    c.assign([1, 0, 0], 2.0)
    c.assign([0, 1, 0], 2.0)
    assert c.assign([1, 0, 0], 0.5) == "說話者2"


def test_leading_short_sentence_is_unknown():
    c = SpeakerClusterer(min_seconds=1.0)
    assert c.assign([1, 0, 0], 0.3) == "?"
    assert c.count == 0
    assert c.assign([1, 0, 0], 1.0) == "說話者1"  # == min -> counts


def test_short_sentence_does_not_update_centroid():
    c = SpeakerClusterer(threshold=0.9, min_seconds=1.0)
    c.assign([1, 0, 0], 2.0)
    for _ in range(20):  # would drag the centroid toward [0,1,0] if it updated
        c.assign([0, 1, 0], 0.5)
    assert c.count == 1
    assert c.assign([0, 1, 0], 2.0) == "說話者2"


def test_configurable_min_seconds():
    c = SpeakerClusterer(min_seconds=3.0)
    assert c.assign([1, 0, 0], 2.0) == "?"
