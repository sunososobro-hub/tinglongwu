"""#1256: shipped clustering defaults from the eval-diarization sweep (run 44338020129): 0.60 / 0.40 / 0.65."""
import math

from tinglongwu.speakers import SpeakerClusterer

LONG, SHORT = 5.0, 1.5
VCTK_CROSS_SPEAKER_MAX = 0.633  # highest centroid cosine between two real VCTK speakers (speakers.py comment)


def _at(cos: float, z: float = 0.0) -> list[float]:
    """A unit vector whose cosine with [1, 0, 0] is `cos` (rest in the y / z plane)."""
    rest = math.sqrt(1 - cos * cos)
    return [cos, rest * math.cos(z), rest * math.sin(z)]


def test_short_reply_after_a_pause_at_cosine_045_keeps_its_speaker():
    c = SpeakerClusterer()
    assert c.assign([1, 0, 0], LONG) == "說話者1"
    assert c.assign(_at(0.45), SHORT) == "說話者1"  # 0.50 short threshold: a new label 說話者2
    assert c.count == 1


def test_clusters_at_cosine_066_are_merged():
    c = SpeakerClusterer()
    c._sums = [[1.0, 0.0, 0.0], _at(0.66)]  # one person split into two clusters whose centroids sit at 0.66
    assert c._merge_near(1) == 0  # 0.70 merge threshold: two labels stay for one person
    assert c.count == 1
    assert c.merged == {"說話者2": "說話者1"}


def test_two_real_speakers_at_the_vctk_ceiling_stay_apart():
    c = SpeakerClusterer()
    c._sums = [[1.0, 0.0, 0.0], _at(VCTK_CROSS_SPEAKER_MAX)]  # the closest two real VCTK speakers
    assert c._merge_near(1) == 1
    assert c.count == 2
    assert c.merged == {}
