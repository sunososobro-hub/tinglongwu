"""Main-process client for the TTS worker subprocess (pipes only, no sockets)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class TTSClient:
    def __init__(self, tts_python: str, model: str, ref: str, ref_text: str = "", xvec_only: bool = False,
                 chunk: int = 12, device: str = "cuda", engine: str = "qwen", speaker: int = 4):
        worker = Path(__file__).with_name("tts_worker.py")
        self.engine, self.speaker = engine, speaker
        if engine == "vosk":
            cmd = [tts_python, str(worker), "--engine", "vosk", "--model", model, "--speaker", str(speaker)]
        else:
            cmd = [tts_python, str(worker), "--model", model, "--ref", ref, "--chunk", str(chunk),
                   "--device", device]
            if xvec_only:
                cmd.append("--xvec-only")
            else:
                cmd += ["--ref-text", ref_text]
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1"}
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

    def speak(self, text: str):
        """Generator of (event_dict, payload_bytes|None); ends after the 'done' (or 'error') event."""
        self._n += 1
        req = {"id": self._n, "text": text}
        if self.engine == "vosk":
            req["speaker"] = self.speaker  # read per request, so set_voice() takes effect on the next sentence
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
