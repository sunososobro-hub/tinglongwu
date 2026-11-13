"""Prompt and context helpers shared by the chat-LLM translation engines (Ollama, Apple FM; #356).

Both engines send a system prompt (translate src -> tgt, output only the translation) and a user turn with the
last couple of translated lines as context, so sentences split by the segmenter read coherently. Language-specific
wording and post-processing come from tinglongwu/lang/<code>/ (#390), never from `if lang == ...` branches here.
"""
from __future__ import annotations

from . import lang as lang_patches

LANG_NAMES = {"en": "English", "zh": "Chinese", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
              "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
              "ar": "Arabic", "hi": "Hindi", "tr": "Turkish", "vi": "Vietnamese", "th": "Thai",
              "id": "Indonesian", "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch"}

# Previous source lines + their translations shown to the model. Short and capped (~300 chars).
CONTEXT_LINES = 2
CONTEXT_MAX_CHARS = 300
CONTEXT_NOTE = ("Earlier lines are context only; do not translate or repeat them. "
                "Translate ONLY the final line (it may continue the previous one).")
DRAFT_MAX_TOKENS = 64  # --stream drafts are re-translated a moment later; keep them short


def build_system_prompt(src: str, tgt: str, zh_variant: str = "tw") -> str:
    s = LANG_NAMES.get(src, src)
    dest = lang_patches.setting(tgt, "LLM_TARGET_NAMES", {}).get(zh_variant) or LANG_NAMES.get(tgt, tgt)
    return (f"You are a professional translator. Translate the user's {s} text into {dest}. "
            "Output only the translation, nothing else.")


def build_user(text: str, context=(), glossary_note: str = "") -> str:
    """User turn: optional glossary note, context pairs (src, translation), then the line to translate."""
    note = f"{glossary_note}\n" if glossary_note else ""
    if not context:
        return f"{note}[Translate]\n{text}" if note else text
    ctx = "\n".join(f"{a} => {b}" for a, b in context)
    return f"{note}{CONTEXT_NOTE}\n[Earlier]\n{ctx}\n[Translate]\n{text}"


def trim_context(history, max_chars: int = CONTEXT_MAX_CHARS, lines: int = CONTEXT_LINES):
    """Last `lines` pairs, dropping the oldest until the total length fits `max_chars`."""
    h = list(history)[-lines:]
    while h and sum(len(a) + len(b) for a, b in h) > max_chars:
        h.pop(0)
    return h


def convert_output(text: str, tgt: str, zh_variant: str) -> str:
    """The target language's post-processing (e.g. OpenCC for Chinese), or the text unchanged."""
    convert = lang_patches.setting(tgt, "convert_output", None)
    return convert(text, zh_variant) if convert else text
