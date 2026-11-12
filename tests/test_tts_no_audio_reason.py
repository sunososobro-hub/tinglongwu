"""#316: in speak mode every translated utterance is spoken, or a one-line reason says why not.

The fake worker below behaves like the real Vosk-TTS ru worker: vosk_tts splits words only at , . ? ! ; : " ( ) and
space, so any other punctuation ends up as a "phone" that is not in phoneme_id_map, and tts_worker.py answers the
request with an {"t": "error"} event instead of audio (seen live: "Ух, уже можно сказать…" -> no audio, no reason).
The real proto/logic code runs; MT, TTS worker and player are fakes."""
import re
from types import SimpleNamespace

import numpy as np

from tinglongwu.reverse import proto
from tinglongwu.reverse.logic import Lat, speech_pieces, tts_text

SEEN = ["Ух, уже можно сказать…", "Я сейчас начинаю записывать… ээ…"]
_VOSK_SPLIT = re.compile(r"""([,.?!;:"() ])""")


class VoskLikeTTS:
    """Errors (like the real worker) when a word still holds a character the Vosk phoneme map does not have."""

    engine = "vosk"

    def __init__(self):
        self.texts = []

    def speak(self, text, lang=None):
        self.texts.append(text)
        for word in _VOSK_SPLIT.split(text.lower()):
            bad = [c for c in word if not (c.isalnum() or c in "-") and not _VOSK_SPLIT.fullmatch(c)]
            if bad and word.strip():
                yield {"t": "error", "msg": f"KeyError({bad[0]!r})"}, None
                return
        yield {"t": "chunk", "sr": 24000}, np.ones(240, np.float32).tobytes()
        yield {"t": "done"}, None


class MT:
    def __init__(self, out):
        self.out = out

    def translate(self, text, src, tgt):
        return SimpleNamespace(text=self.out)


class Player:
    enabled = True

    def __init__(self):
        self.pushed = []

    def push(self, audio, sr):
        self.pushed.append(len(audio))


def _live(tts, ru):
    res, player = {"items": []}, Player()
    proto._run_auto_live(iter([(1, "mic", "我")]), MT(ru), tts, player, res, "ru", Lat())
    return res["items"][0], player


def test_seen_utterances_have_pieces_and_are_spoken(capsys):
    for ru in SEEN:
        assert speech_pieces(ru)
        tts = VoskLikeTTS()
        item, player = _live(tts, ru)
        assert item["decision"] == "sent" and item["pieces"] >= 1 and player.pushed, (ru, tts.texts)
        assert "…" not in "".join(tts.texts)
    out = capsys.readouterr()
    assert "first_audio=-s" not in out.err and "nothing to speak" not in out.out


def test_tts_text_keeps_words_and_rewrites_punctuation_vosk_cannot_read():
    assert tts_text("записывать… ээ…") == "записывать... ээ..."
    assert tts_text("Это «тест» — да") == "Это тест, да"
    assert tts_text("Hello, world.") == "Hello, world."


def test_worker_error_is_reported_on_one_line(capsys):
    class Broken:
        def speak(self, text, lang=None):
            yield {"t": "error", "msg": "KeyError('§')"}, None

    item, player = _live(Broken(), "Привет.")
    out = capsys.readouterr()
    assert item["decision"] == "no_audio" and not player.pushed
    lines = [ln for ln in out.out.splitlines() if ln.startswith("[tts] nothing to speak:")]
    assert lines == ["[tts] nothing to speak: TTS error: KeyError('§')"]
    assert "first_audio=-s" in out.err  # the latency line is unchanged, but no longer without a reason


def test_voice_without_audio_and_empty_translation_are_reported(capsys):
    class Mute:  # e.g. FollowTTS with the Russian voice not installed: no events at all
        def speak(self, text, lang=None):
            return iter(())

    _live(Mute(), "Привет.")
    assert "[tts] nothing to speak: the voice returned no audio" in capsys.readouterr().out
    _live(VoskLikeTTS(), "…")
    assert "[tts] nothing to speak: no speakable text in the translation" in capsys.readouterr().out


def test_empty_chunk_is_not_counted_as_audio(capsys):
    class EmptyChunk:
        def speak(self, text, lang=None):
            yield {"t": "chunk", "sr": 24000}, b""
            yield {"t": "done"}, None

    item, player = _live(EmptyChunk(), "Привет.")
    assert item["decision"] == "no_audio" and not player.pushed
    assert "[tts] nothing to speak:" in capsys.readouterr().out
