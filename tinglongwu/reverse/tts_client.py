"""Main-process client for the TTS worker subprocess (pipes only, no sockets)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class TTSClient:
    def __init__(self, tts_python: str, model: str, engine: str = "vosk", speaker: int = 4, lang: str = "en"):
        worker = Path(__file__).with_name("tts_worker.py")
        self.engine, self.speaker, self.lang = engine, speaker, lang  # lang: kokoro only (en | ja)
        if engine not in ("vosk", "kokoro", "piper"):
            raise ValueError(f"unknown TTS engine {engine!r} (built-in fixed voices only: vosk, kokoro, piper)")
        cmd = [tts_python, str(worker), "--engine", engine, "--model", model, "--speaker", str(speaker)]
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1",
               "TLWU_MODE": "worker"}
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.ready = self._read_header()
        if self.ready.get("t") != "ready":
            raise RuntimeError(f"TTS worker failed to start: {self.ready}")
        self._n = 0

    def _read_header(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"TTS worker exited (code {self.proc.poll()})")
        return json.loads(line)

    def speak(self, text: str, lang: str | None = None):
        """Generator of (event_dict, payload_bytes|None); ends after the 'done' (or 'error') event."""
        self._n += 1
        req = {"id": self._n, "text": text}
        if self.engine in ("vosk", "kokoro"):
            req["speaker"] = self.speaker  # read per request, so set_voice() takes effect on the next sentence
        if self.engine == "kokoro":
            req["lang"] = lang or self.lang
        self.proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        while True:
            h = self._read_header()
            data = self.proc.stdout.read(h["n"]) if "n" in h else None
            yield h, data
            if h["t"] in ("done", "error"):
                return

    def close(self) -> None:
        try:
            self.proc.stdin.write(b'{"cmd": "quit"}\n')
            self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            self.proc.kill()
