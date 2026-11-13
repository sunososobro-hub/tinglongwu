"""#608: topic segmenter (pause / min / max / embedding-similarity drop / flush)."""
import pytest

from tinglongwu.topic_segmenter import MAX_S, MIN_S, PAUSE_S, TopicSegmenter
from tinglongwu.topics import Line, PauseSegmenter


def L(t, text="x", side="them"):
    return Line(float(t), side, text)


# fake embedding: each topic word maps to its own axis, so lines about different things are orthogonal
AXES = {"budget": 0, "hiring": 1, "travel": 2}


def fake_embed(text):
    v = [0.0] * len(AXES)
    for w in text.split():
        if w in AXES:
            v[AXES[w]] += 1.0
    return v


def feed(seg, lines):
    out = []
    for ln in lines:
        out += seg.add(ln)
    return out


def texts(topic):
    return [ln.text for ln in topic]


def test_defaults_match_595():
    assert (PAUSE_S, MIN_S, MAX_S) == (8.0, 60.0, 300.0)


def test_pause_closes_a_topic_once_it_is_min_long():
    seg = TopicSegmenter()
    assert feed(seg, [L(0, "a"), L(30, "b"), L(60, "c")]) == []
    closed = seg.add(L(68, "d"))  # exactly an 8 s pause on a 60 s topic
    assert [texts(t) for t in closed] == [["a", "b", "c"]]
    assert texts(seg.flush()[0]) == ["d"]


def test_pause_below_threshold_keeps_topic_open():
    seg = TopicSegmenter()
    assert feed(seg, [L(0), L(60), L(67.9)]) == []  # 7.9 s < 8 s


def test_no_topic_shorter_than_min_except_flush():
    seg = TopicSegmenter(embed=fake_embed)
    # long pauses and a total topic change, but the open topic is only 40 s long: nothing closes
    lines = [L(0, "budget"), L(20, "hiring"), L(40, "travel")]
    assert feed(seg, lines) == []
    (rest,) = seg.flush()
    assert texts(rest) == ["budget", "hiring", "travel"] and rest[-1].t - rest[0].t < MIN_S
    assert seg.flush() == []


def test_no_topic_longer_than_max():
    seg = TopicSegmenter()
    closed = feed(seg, [L(i * 5, str(i)) for i in range(400)])  # one line every 5 s, never a pause
    closed += seg.flush()
    assert len(closed) >= 6
    assert all(t[-1].t - t[0].t <= MAX_S for t in closed)
    assert all(t[-1].t - t[0].t >= MIN_S for t in closed[:-1])
    assert [ln.text for t in closed for ln in t] == [str(i) for i in range(400)]  # nothing lost or reordered


def test_embedding_similarity_drop_closes_a_topic():
    seg = TopicSegmenter(embed=fake_embed)
    budget = [L(i * 5, "budget q3") for i in range(14)]  # 0..65 s, no pause
    assert feed(seg, budget) == []
    closed = seg.add(L(70, "hiring plan"))  # no pause, but orthogonal to the open topic
    assert len(closed) == 1 and texts(closed[0]) == ["budget q3"] * 14
    assert texts(seg.flush()[0]) == ["hiring plan"]


def test_similar_line_keeps_topic_open():
    seg = TopicSegmenter(embed=fake_embed)
    assert feed(seg, [L(i * 5, "budget") for i in range(14)] + [L(70, "budget hiring budget")]) == []


def test_without_embed_a_topic_change_does_not_close():
    seg = TopicSegmenter()
    assert feed(seg, [L(i * 5, "budget") for i in range(14)] + [L(70, "hiring")]) == []


def test_thresholds_are_parameters():
    seg = TopicSegmenter(pause_s=2, min_s=5, max_s=20, embed=fake_embed, sim_threshold=0.9, window=1)
    assert feed(seg, [L(0, "budget"), L(5, "budget")]) == []
    assert len(seg.add(L(7, "budget"))) == 1  # 2 s pause on a 5 s topic
    assert feed(seg, [L(8, "budget"), L(12, "budget")]) == []
    assert len(seg.add(L(13, "budget hiring"))) == 1  # cosine 0.71 < 0.9
    assert feed(seg, [L(14 + i, "hiring budget") for i in range(20)]) == []  # 13..33 s, 1 s gaps, same topic
    assert len(seg.add(L(34, "hiring budget"))) == 1  # 34 - 13 > 20: max cut
    with pytest.raises(ValueError):
        TopicSegmenter(min_s=400, max_s=300)


def test_window_uses_recent_lines_only():
    # topic drifts budget -> hiring; with window=2 the centroid follows the drift, so "hiring" does not close
    lines = [L(i * 5, "budget") for i in range(10)] + [L(50 + i * 5, "hiring") for i in range(4)]
    drift = TopicSegmenter(embed=fake_embed, window=2)
    assert feed(drift, lines + [L(70, "hiring")]) == []
    wide = TopicSegmenter(embed=fake_embed, window=14)
    assert len(feed(wide, lines + [L(70, "hiring")])) == 1


def test_embed_error_falls_back_to_pause_rules():
    def boom(text):
        raise RuntimeError("no model")

    seg = TopicSegmenter(embed=boom)
    assert feed(seg, [L(i * 5, "budget") for i in range(14)] + [L(70, "hiring")]) == []
    assert len(seg.add(L(80, "travel"))) == 1  # the 10 s pause still closes


def test_long_silence_after_short_topic_cuts_at_max():
    seg = TopicSegmenter()
    assert feed(seg, [L(0, "a"), L(10, "b")]) == []
    closed = seg.add(L(400, "c"))  # keeping "a b" open would make a 400 s topic
    assert [texts(t) for t in closed] == [["a", "b"]]


def test_topics_pause_segmenter_is_the_608_segmenter():
    seg = PauseSegmenter()
    assert isinstance(seg, TopicSegmenter) and seg.embed is None
