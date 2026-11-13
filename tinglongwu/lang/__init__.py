"""Per-language patches (#390 convention): language-specific behaviour lives in tinglongwu/lang/<code>/, never as
`if lang == "xx"` branches in core modules. Core code asks for a named value by language code and passes a
language-neutral default; a language that needs something else defines that name in its own package and is listed
in PATCHES below (a static map: no dynamic imports, see tests/test_no_network_code.py).

Known names:
- MERGE_PAUSE_SEND (float, seconds): VAD silence after a non-forced segment that releases an unfinished merge
  buffer (core default: merge.PAUSE_SEND). #327.
- LLM_TARGET_NAMES (dict, zh variant -> text): how the chat-LLM engines (llm_prompt.py) name this target language
  in their system prompt (core default: the English language name). #356.
- convert_output (callable(text, variant) -> text): post-processing of chat-LLM output in this target language
  (core default: none). #356.
"""
from __future__ import annotations

from types import ModuleType
from typing import Any

from . import ja, zh

PATCHES: dict[str, ModuleType] = {"ja": ja, "zh": zh}


def _base(lang: str) -> str:
    return (lang or "").split("-")[0].split("_")[0].lower()


def module(lang: str) -> ModuleType | None:
    """tinglongwu.lang.<code> for this language code ("ja", "ja-JP"), or None when it has no patches."""
    return PATCHES.get(_base(lang))


def setting(lang: str, name: str, default: Any) -> Any:
    """The language's value for `name`, or `default` (the core's language-neutral value)."""
    return getattr(module(lang), name, default)
