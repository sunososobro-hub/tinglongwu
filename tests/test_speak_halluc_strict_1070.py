"""#1070: speak leg drops YouTube sign-off hallucinations even when they are only part of the text."""
from tinglongwu.hallucination import is_hallucination
from tinglongwu.reverse.gate import SegmentStats, gate

LINE_TW = "請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目"
LINE_CN = "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"


def test_speak_gate_drops_signoff_at_normal_energy():
    for t in (LINE_TW, LINE_CN):
        assert gate(SegmentStats(2.0, 0.05, t, 0.1, -0.3), lang="zh") == (False, "repeat")


def test_real_sentence_still_passes():
    assert gate(SegmentStats(2.0, 0.05, "我們明天下午三點開會好嗎", 0.1, -0.3), lang="zh") == (True, "")


def test_listen_side_unchanged_without_strict():
    assert not is_hallucination("我覺得這個訂閱方案太貴了我們再討論一下價格吧", 0.05, 0.0)
    assert not is_hallucination("我覺得這個訂閱方案太貴了我們再討論一下價格吧", 0.05, 0.0, strict=False)


def test_strict_drops_signoff_direct():
    for t in (LINE_TW, LINE_CN):
        assert is_hallucination(t, 0.05, 0.0, strict=True)


def test_non_strict_behaviour_unchanged():
    # default call and strict=False must agree
    for t in (LINE_TW, LINE_CN, "我們明天下午三點開會"):
        assert is_hallucination(t, 0.05, 0.0) == is_hallucination(t, 0.05, 0.0, strict=False)


def test_normal_sentence_passes_both_modes():
    t = "我們明天下午三點開會"
    assert not is_hallucination(t, 0.05, 0.0, strict=True)
    assert not is_hallucination(t, 0.05, 0.0, strict=False)


def test_strict_keeps_spoken_courtesy_at_normal_energy():
    # #312: "Thank you" / "謝謝大家" said into a meeting must still be sent; only quiet / no-speech drops them
    for t in ("Thank you", "謝謝大家", "감사합니다"):
        assert gate(SegmentStats(1.5, 0.05, t, 0.05, -0.3)) == (True, "")
        assert is_hallucination(t, 0.005, 0.0, strict=True)
