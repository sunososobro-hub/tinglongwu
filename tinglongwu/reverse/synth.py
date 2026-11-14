"""Speak-mode TTS side (split from proto.py, #663): streamed synthesis, warm-up, --to follow voices."""
from __future__ import annotations

import re
import time

import numpy as np


def _file_items(wavs, sf, asr):
    """(n, label, zh) for each wav file with recognised speech."""
    for n, wav in enumerate(wavs, 1):
        audio, sr = sf.read(str(wav), dtype="float32")
        assert sr == 16000, f"{wav}: expected 16 kHz"
        tr = asr.transcribe(audio)
        if tr and tr.text:
            yield n, wav.name, tr.text


VOICE_ENGINES = ("vosk", "kokoro")  # engines with built-in voices (v0-v4 switch)


def _synth_sentences(tts, ru: str, stats: list | None = None, clock=None, on_first=None, info: dict | None = None):
    """Yield (audio, sr) one piece at a time, so the first piece can play while the rest is synthesized. #207: the
    first piece is a short clause (PiecePlanner), so the first audio does not wait for a long first sentence; each
    later piece is sized from this worker's measured speed (chars/s) so it is ready before the audio queued ahead
    of it has played (no gap), and is as long as that allows (fewer cuts). The worker's 'done' events are appended
    to `stats` when given; on_first(seconds) is called once, when the first piece is ready (before it is yielded).
    #316: `info` (a dict, when given) gets "pieces" (TTS requests sent) and "errors" (worker error messages), so a
    caller that got no audio can say why."""
    from .logic import PiecePlanner, next_budget, tts_text

    if info is not None:
        info.setdefault("pieces", 0)
        info.setdefault("errors", [])

    clock = clock or time.perf_counter
    t0 = clock()
    plan, end, cps = PiecePlanner(ru), None, None  # end: when the audio handed out so far finishes playing
    budget = None
    while (sent := plan.next(budget)) is not None:
        t1 = clock()
        pcm, sr_out = [], 24000
        if info is not None:
            info["pieces"] += 1
        for ev, data in tts.speak(tts_text(sent)):
            if ev["t"] == "chunk":
                if data:
                    pcm.append(np.frombuffer(data, dtype=np.float32))
                sr_out = ev["sr"]
            elif ev["t"] == "done" and stats is not None:
                stats.append(ev)
            elif ev["t"] == "error" and info is not None:
                info["errors"].append(str(ev.get("msg") or "unknown error"))
        now = clock()
        if now > t1:  # wall time incl. the pipe: what the next piece will really cost
            c = len(sent) / (now - t1)
            cps = c if cps is None else min(c, 0.5 * (cps + c))  # pessimistic: a slow piece counts at once
        if pcm:
            audio = np.concatenate(pcm)
            if end is None and on_first is not None:
                on_first(now - t0)
            end = max(now, end or now) + len(audio) / sr_out
            yield audio, sr_out
        budget = next_budget((end or clock()) - clock(), cps)


# A full-length sentence, not "Hello.": onnxruntime's first runs at a new input length are slower (allocations),
# measured: the first real utterance after a 1-word warm-up still had a 0.6-0.7 s gap between its pieces
WARM_TEXT = {"ru": "Здравствуйте, давайте начнём сегодняшнюю встречу и обсудим планы на следующую неделю.",
             "ja": "皆さん、こんにちは。今日の会議を始めましょう。",
             "zh": "大家好，我們現在開始今天的會議，先看一下上週的進度。"}
WARM_EN = "Hello everyone, let's begin today's meeting and look at the plans for next week."


def warm_tts(tts, lang: str) -> None:
    """#207: one throw-away request at startup (phonemizer / onnxruntime first-run cost), so the first utterance of
    the session is not slower than the rest. Failures are left to the real requests to report."""
    try:
        for _ in tts.speak(WARM_TEXT.get(lang, WARM_EN)):
            pass
    except Exception:  # noqa: BLE001
        pass


def _make_synth(tts):
    """Confirm-mode synth: a generator of sentence parts, run by the session's TTS worker; after "y" each part is
    sent as soon as it exists, so sentence 1 plays while the rest is still synthesized (#139)."""
    infos: dict = {}  # ru text -> info of its latest synthesis (#368: why no audio)

    def synth(ru: str):
        info = infos[ru] = {}
        return _synth_sentences(tts, ru, info=info)

    synth.no_audio_reason = lambda ru: no_audio_reason(infos.get(ru) or {})  # type: ignore[attr-defined]
    return synth


def _target(a) -> str:
    """Spoken language for the next sentence; with --to follow, whatever the other side spoke last (default en)."""
    to = getattr(a, "to", "ru")
    if to == "follow":
        from ..langfollow import default_follow_path, read_lang

        return read_lang(getattr(a, "follow_file", None) or default_follow_path(), "en")
    return to


_CYR = re.compile("[Ѐ-ӿ]")
_JA = re.compile("[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")  # kana / ideographs (en and ru output never has them)


class FollowTTS:
    """TTS front for --to follow: Russian (Cyrillic) text goes to the Vosk worker, anything else to Kokoro
    (Japanese text is Kokoro too, with lang "ja": same worker process, misaki loads on the first ja sentence).
    Each worker subprocess is started on first use (`factories`: {"en": callable, "ru": callable})."""

    engine = "vosk"  # both built-in-voice engines: v0-v4 switching works
    ready: dict = {}

    def __init__(self, factories: dict):
        self.factories, self.clients, self._speaker = factories, {}, None

    def client(self, lang: str):
        if lang not in self.clients:
            print(f"loading {lang} voice...", flush=True)
            c = self.factories[lang]()
            if self._speaker is not None:
                c.speaker = self._speaker
            self.clients[lang] = c
        return self.clients[lang]

    @property
    def speaker(self):
        return self._speaker

    @speaker.setter
    def speaker(self, n: int) -> None:
        self._speaker = n
        for c in self.clients.values():
            c.speaker = n

    def speak(self, text: str):
        if _CYR.search(text):
            if "ru" not in self.factories:  # Russian voice model not installed (opt-in): subtitles only
                return iter(())
            return self.client("ru").speak(text)
        if _JA.search(text):
            return self.client("en").speak(text, lang="ja")
        return self.client("en").speak(text, lang="en")

    @property
    def proc(self):
        return self

    def kill(self) -> None:
        for c in self.clients.values():
            try:
                c.proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        for c in self.clients.values():
            c.close()


def no_audio_reason(info: dict) -> str:
    """#316: one short reason why a translated utterance produced no audio (info from _synth_sentences)."""
    if info.get("errors"):
        return f"TTS error: {info['errors'][0]}"
    if not info.get("pieces"):
        return "no speakable text in the translation"
    return "the voice returned no audio (voice for this language not installed?)"


def describe_error(e: BaseException) -> str:
    """'Type: message' (just the type when the message is empty, e.g. a bare AssertionError)."""
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
