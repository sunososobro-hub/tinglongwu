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
           "Корректор А.Кулакова", "Субтитры делал", "Подписывайтесь на канал"],
    "en": ["Subtitles by", "Subtitles made by", "Subtitles by the Amara.org community",
           "Transcribed by", "Translated by", "Captions by", "Closed captioning by",
           "To be continued", "www.mooji.org"],
    "ja": ["字幕作成", "字幕は", "次回に続く"],
    "zh": ["字幕by", "字幕由","字幕志愿者", "字幕志願者", "字幕製作", "字幕制作", "字幕提供", "中文字幕", "明鏡與點點欄目",
           "未完待續", "未完待续", "謝謝觀看", "谢谢观看"],
    "ko": ["자막 제공", "시청해 주셔서 감사합니다", "다음 영상에서 만나요"],
    "de": ["Untertitel der Amara.org-Community", "Untertitel von", "Untertitelung des ZDF", "Fortsetzung folgt"],
    "fr": ["Sous-titres réalisés par", "Sous-titrage", "Sous-titres par", "À suivre"],
    "es": ["Subtítulos realizados por", "Subtítulos por la comunidad de Amara.org", "Continuará"],
}

WEAK = {
    "ru": ["Спасибо за просмотр", "Спасибо за внимание", "Всем спасибо", "Спасибо что смотрели",
           "Благодарю за внимание", "До новых встреч", "Пока пока"],
    "en": ["Thanks for watching", "Thank you for watching", "Thank you so much for watching",
           "Please subscribe", "Like and subscribe", "See you next time", "See you in the next video",
           "Bye bye", "Thank you"],
    "ja": ["ご視聴ありがとうございました", "ご視聴ありがとうございます", "チャンネル登録", "最後までご視聴"],
    "zh": ["請不吝點贊", "请不吝点赞", "訂閱", "订阅我的频道", "感謝收看", "感谢收看", "謝謝大家", "谢谢大家",
           "歡迎訂閱", "点赞订阅", "點贊訂閱", "下期再見", "下期再见"],
    "ko": ["구독과 좋아요", "감사합니다"],
    "de": ["Danke fürs Zuschauen", "Vielen Dank fürs Zuschauen", "Bis zum nächsten Mal"],
    "fr": ["Merci d'avoir regardé", "Merci de votre attention", "Abonnez-vous"],
    "es": ["Gracias por ver", "Gracias por ver el video", "Suscríbete"],
}

QUIET_RMS = 0.01      # segment RMS below this = "low energy" (speech normally sits well above)
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


def is_hallucination(text: str, rms: float | None = None, no_speech: float = 0.0) -> bool:
    """True if `text` is (mostly) a known Whisper credit/sign-off hallucination.

    rms: RMS of the segment audio (None = unknown, treated as normal energy)."""
    n = _norm(text)
    if not n:
        return False
    cover, strong = _best_cover(n)
    if cover == 0:
        return False
    quiet = (rms is not None and rms < QUIET_RMS) or no_speech >= NO_SPEECH_MIN
    if quiet:
        return cover >= QUIET_COVER
    return strong and cover >= MIN_COVER
