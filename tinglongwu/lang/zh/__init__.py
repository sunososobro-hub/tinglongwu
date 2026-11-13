"""Chinese patches (see tinglongwu/lang/__init__.py)."""

# #356: how the chat-LLM engines (Ollama, Apple FM) name a Chinese target in their system prompt, per --zh-variant.
# Their output is still converted afterwards (convert_output), so the prompt only steers vocabulary and punctuation.
LLM_TARGET_NAMES = {
    "tw": "Traditional Chinese (繁體中文) as written in Taiwan, using natural Taiwan vocabulary and full-width "
          "punctuation",
    "cn": "Simplified Chinese (简体中文) with full-width punctuation",
}


def convert_output(text: str, variant: str) -> str:
    """OpenCC script conversion of chat-LLM output to the requested variant (tw / cn)."""
    from ...translate import zh_convert

    return zh_convert(text, variant)
