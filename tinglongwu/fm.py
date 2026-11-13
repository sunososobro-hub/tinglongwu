"""Apple Foundation Model engine (--engine fm): direct src -> target through macOS 27's built-in `fm` command.

Runs fully on-device (`--model system`; the Private Cloud Compute model is never used). One `fm respond`
process per sentence; the system prompt goes in --instructions, the text in stdin. Prompt, context and glossary
handling are shared with the Ollama engine (llm_prompt.py).

Opt-in and experimental (#356): `--engine fm` must be chosen by hand, `auto` never picks it, and it only runs on
macOS (the `fm` command ships with macOS 27 on Apple silicon); not yet tested by the maintainers.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from collections import deque

from .engines import clean_output
from .llm_prompt import CONTEXT_LINES, build_system_prompt, build_user, convert_output, trim_context
from .repeat import collapse_repeats
from .translate import Translation, Translator

TIMEOUT_S = 60
NOT_FOUND = "the `fm` command was not found: --engine fm needs macOS 27 or later on Apple silicon."


def _run(cmd: list[str], stdin: str) -> tuple[int, str, str]:
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=TIMEOUT_S)
    return p.returncode, p.stdout, p.stderr


class FmTranslator(Translator):
    """Direct src -> tgt translation with Apple's on-device model; OpenCC afterwards when tgt is zh."""

    def __init__(self, zh_variant: str = "tw", runner=None, binary: str | None = None):
        self.zh_variant = zh_variant
        self._runner = runner or _run  # (cmd, stdin) -> (returncode, stdout, stderr); injectable for tests
        # macOS only: on Windows/Linux an unrelated `fm` on PATH must never be run (it could open a console window)
        found = shutil.which("fm") if sys.platform == "darwin" else None
        self.binary = binary or found or ("fm" if runner else "")
        if not self.binary:
            raise RuntimeError(NOT_FOUND)
        self.history: deque = deque(maxlen=CONTEXT_LINES)
        self._pair: tuple[str, str] | None = None
        self.glossary = None  # set by the CLI, same contract as the other LLM engines

    def _respond(self, instructions: str, prompt: str) -> str:
        cmd = [self.binary, "respond", "--model", "system", "--no-stream", "--greedy",
               "--guardrails", "permissive-content-transformations", "--instructions", instructions]
        try:
            code, out, err = self._runner(cmd, prompt)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"fm did not answer within {TIMEOUT_S} s") from e
        if code != 0:
            hint = " Run `sudo fm license` once to accept Apple's terms." if code == 69 else ""
            raise RuntimeError(f"fm exited with {code}: {err.strip()[:200]}{hint}")
        return out

    def preload(self, src: str = "", tgt: str = "") -> None:
        """Fail early (license not accepted, model not ready) and warm the model up."""
        self._respond("Reply with the single word: ok", "ok")

    def draft(self, text: str, src: str, tgt: str) -> Translation:
        return self.translate(text, src, tgt, draft=True)

    def translate(self, text: str, src: str, tgt: str, draft: bool = False) -> Translation:
        if src == tgt or not text.strip():
            return Translation(None, text)
        text = collapse_repeats(text)
        if self._pair != (src, tgt):
            if draft:  # drafts never change state: a new pair just gets no context
                return self._translate(text, src, tgt, draft, ())
            self.history.clear()
            self._pair = (src, tgt)
        return self._translate(text, src, tgt, draft, trim_context(self.history))

    def _translate(self, text: str, src: str, tgt: str, draft: bool, ctx) -> Translation:
        note = self.glossary.prompt_block(text, *(a for a, _ in ctx)) if self.glossary else ""
        out = clean_output(self._respond(build_system_prompt(src, tgt, self.zh_variant),
                                         build_user(text, ctx, note)))
        out = convert_output(out, tgt, self.zh_variant)
        if out and not draft:
            self.history.append((text.strip(), out))
        return Translation(None, out)
