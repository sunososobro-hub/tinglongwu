"""Whisper hallucination filter. On music / silence / noise Whisper (trained on subtitled video) emits stock
subtitle-credit and sign-off lines. They are dropped here. All phrases live in the two tables below.

STRONG: never plausible as real speech (credits, "to be continued") -> dropped whenever they make up most of
the text. WEAK: could be genuinely said ("thanks for watching") -> dropped only when the segment is quiet
(RMS < QUIET_RMS) or Whisper's no_speech_prob is high (>= NO_SPEECH_MIN)."""
from __future__ import annotations

import re

STRONG = {
    "ru": ["Продолжение следует", "Субтитры сделал DimaTorzok", "Субтитры создавал DimaTorzok",
           "Субтитры подогнал", "Субтитры добавил", "Редактор субтитров", "Корректор А.Егорова",
           "Корректор А.Кулакова", "Субтитры делал", "Подписывайтесь на канал",
           "Спасибо за субтитры", "Благодарим за субтитры",
           # #1255: channel sign-offs / credits Whisper emits on silence in ru meetings
           "Подпишитесь на канал", "Подписывайтесь на наш канал", "Подпишитесь на наш канал",
           "Подписаться на канал", "Ставьте лайки", "Субтитры создавались"],
    "en": ["Subtitles by", "Subtitles made by", "Subtitles by the Amara.org community",
           "Transcribed by", "Translated by", "Captions by", "Closed captioning by",
           "To be continued", "www.mooji.org", "YoYo Television Series Exclusive"],
    "ja": ["字幕作成", "字幕は", "次回に続く"],
    "zh": ["字幕by", "字幕由","字幕志愿者", "字幕志願者", "字幕製作", "字幕制作", "字幕提供", "中文字幕", "明鏡與點點欄目", "明镜与点点栏目",
           "打賞支持", "打赏支持", "點贊訂閱轉發", "点赞订阅转发",
           "未完待續", "未完待续", "謝謝觀看", "谢谢观看",
           "優優獨播劇場", "优优独播剧场"],
    "ko": ["자막 제공", "시청해 주셔서 감사합니다", "다음 영상에서 만나요"],
    "de": ["Untertitel der Amara.org-Community", "Untertitel von", "Untertitelung des ZDF", "Fortsetzung folgt"],
    "fr": ["Sous-titres réalisés par", "Sous-titrage", "Sous-titres par", "À suivre"],
    "es": ["Subtítulos realizados por", "Subtítulos por la comunidad de Amara.org", "Continuará"],
}

WEAK = {
    "ru": ["Спасибо за просмотр", "Спасибо за внимание", "Всем спасибо", "Спасибо что смотрели",
           "Благодарю за внимание", "До новых встреч", "Пока пока"],
    "en": ["Thanks for watching", "Thank you for watching", "Thank you so much for watching",
           "Please subscribe", "Like and subscribe", "Subscribe to our channel", "Subscribe to my channel", "See you next time", "See you in the next video",
           "Bye bye", "Bye", "Thank you", "Thanks"],
    "ja": ["ご視聴ありがとうございました", "ご視聴ありがとうございます", "チャンネル登録", "最後までご視聴"],
    "zh": ["請不吝點贊", "请不吝点赞", "訂閱", "订阅我的频道", "感謝收看", "感谢收看", "謝謝大家", "谢谢大家",
           "歡迎訂閱", "点赞订阅", "點贊訂閱", "下期再見", "下期再见"],
    "ko": ["구독과 좋아요", "감사합니다"],
    "de": ["Danke fürs Zuschauen", "Vielen Dank fürs Zuschauen", "Bis zum nächsten Mal"],
    "fr": ["Merci d'avoir regardé", "Merci de votre attention", "Abonnez-vous"],
    "es": ["Gracias por ver", "Gracias por ver el video", "Suscríbete"],
}

# Lone filler words Whisper invents on silence; dropped regardless of energy (nobody transcribes a bare "you").
LONE = {"you"}

# #1247: a segment below this RMS (-80 dBFS) is skipped before ASR as silence. It was 1e-3 (-60 dBFS) in pipeline.py and
# stream.py, which dropped real quiet speech: the #894 check's --file pass (bench-cpu-ab live-file, #1324 @99aaec2)
# missed 13/31 FLEURS clips whose RMS is -61.1 to -66 dBFS (median -63.2) vs -48.6 dBFS for the heard ones, and a
# far lecture mic (#894) sits at that level too. Digital silence / an idle CABLE is below -90 dBFS, so the skip still
# keeps Whisper off empty audio; quiet segments that reach ASR still get the QUIET_RMS hallucination rule below.
SILENCE_RMS = 1e-4
QUIET_RMS = 0.015     # segment RMS below this = "low energy" (speech normally sits well above)
NO_SPEECH_MIN = 0.3   # Whisper no_speech_prob at/above this = "probably not speech"
MIN_COVER = 0.6       # the phrase must make up at least this share of the text (else it is part of real speech)
QUIET_COVER = 0.3     # ... or this share when the segment is quiet / no-speech


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower().replace("ё", "е"))


def _compile() -> list[tuple[str, bool]]:
    return [(_norm(p), strong) for t, strong in ((STRONG, True), (WEAK, False))
            for ps in t.values() for p in ps if _norm(p)]


_PHRASES = _compile()


def _best_cover(norm: str) -> tuple[float, bool]:
    """Largest share of `norm` covered by a listed phrase, and whether that phrase is a strong one."""
    best, best_strong = 0.0, False
    for p, strong in _PHRASES:
        if p in norm:
            c = 1.0 if strong and norm.startswith(p) else len(p) / len(norm)  # credit line + a name: all of it
            if (c, strong) > (best, best_strong):
                best, best_strong = c, strong
    return best, best_strong


def is_hallucination(text: str, rms: float | None = None, no_speech: float = 0.0, strict: bool = False) -> bool:
    """True if `text` is (mostly) a known Whisper credit/sign-off hallucination.

    rms: RMS of the segment audio (None = unknown, treated as normal energy).
    strict (#1070, speak leg): the text is spoken into someone else's meeting, so silence beats a wrong sentence:
    any STRONG phrase anywhere drops it. WEAK phrases keep the energy rule: "Thank you" / "謝謝大家" are real
    things people say in a meeting (#312), so they are dropped only when the segment is quiet or no-speech."""
    n = _norm(text)
    if not n:
        return False
    if n in LONE:
        return True
    cover, strong = _best_cover(n)
    if cover == 0:
        return False
    if strict and strong:
        return True
    quiet = (rms is not None and rms < QUIET_RMS) or no_speech >= NO_SPEECH_MIN
    if quiet:
        return cover >= QUIET_COVER
    return strong and cover >= MIN_COVER
