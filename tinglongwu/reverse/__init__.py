"""Reverse mode prototype: Chinese speech -> Russian synthesized speech (ASR -> Hy-MT2 MT -> built-in-voice TTS: Vosk-TTS / Kokoro).

Separate from the main tlwu path. TTS runs in a subprocess with its own venv over stdin/stdout pipes.
"""
