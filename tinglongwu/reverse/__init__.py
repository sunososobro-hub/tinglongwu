"""Reverse mode prototype: Chinese speech -> Russian synthesized speech (ASR -> Qwen3 MT -> Qwen3-TTS).

Separate from the main tlwu path. TTS runs in a subprocess with its own venv (torch cu128) over stdin/stdout pipes.
"""
