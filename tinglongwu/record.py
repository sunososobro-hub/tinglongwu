"""Opt-in local recording (#239): `--record [PATH]` writes the captured audio to a 16 kHz mono WAV next to the
transcripts. Off by default; when off nothing here is imported and no audio file is ever created. Local only:
no network code in this module."""
from __future__ import annotations

import datetime
import wave
from typing import Iterator

import numpy as np

from .capture.base import SAMPLE_RATE, AudioSource
from .i18n import MESSAGES


MESSAGES["en"].update(rec_label="● Recording", rec_consent="Recording is on: audio is saved on this computer only. "
                      "Get the other side's consent before recording their voice.")
MESSAGES["ru"].update(rec_label="● Запись", rec_consent="Запись включена: звук сохраняется только на этом компьютере. "
                      "Перед записью голоса собеседника получите его согласие.")
MESSAGES["zh-TW"].update(rec_label="● 錄音中", rec_consent="錄音已開啟：聲音只存在這台電腦。"
                         "錄下對方的聲音前，請先取得對方同意。")


def rec_label() -> str:
    from .i18n import t

    return t("rec_label")


def consent_notice() -> str:
    from .i18n import t

    return t("rec_consent")


def default_record_path(now=None) -> str:
    return f"transcripts/{(now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')}.wav"


class RecordingSource(AudioSource):
    """Passes frames through unchanged and appends them to a WAV file (16-bit PCM, flushed per frame)."""

    def __init__(self, inner: AudioSource, path):
        from pathlib import Path

        self.inner, self.path = inner, Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._w = wave.open(str(self.path), "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(SAMPLE_RATE)

    def frames(self) -> Iterator[np.ndarray]:
        for f in self.inner.frames():
            pcm = (np.clip(np.asarray(f, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
            self._w.writeframes(pcm.tobytes())
            yield f

    def close(self) -> None:
        try:
            self._w.close()
        finally:
            self.inner.close()
