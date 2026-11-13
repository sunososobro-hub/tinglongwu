"""Ollama translation engine (--engine ollama): direct src -> target through a LOCAL Ollama server.

Only loopback URLs are accepted (127.0.0.1 / localhost / ::1), so nothing leaves the machine. Thinking mode is
switched off in the request and any <think> output is stripped. Prompt, context and glossary handling are shared
with the Apple FM engine (llm_prompt.py).

Opt-in only (#356): `--engine ollama` must be chosen by hand; `auto` never picks it, and tlwu never installs Ollama
or pulls a model. Without --ollama-model / TLWU_OLLAMA_MODEL the first model already installed in Ollama is used.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
from collections import deque
from urllib.parse import urlsplit

from .offline import is_loopback_host
from .engines import clean_output, max_tokens_for
from .llm_prompt import (CONTEXT_LINES, DRAFT_MAX_TOKENS, build_system_prompt, build_user, convert_output,
                         trim_context)
from .repeat import collapse_repeats
from .translate import Translation, Translator

DEFAULT_URL = "http://127.0.0.1:11434"
TIMEOUT_S = 120  # first request may include loading the model into memory


def default_url() -> str:
    return os.environ.get("TLWU_OLLAMA_URL") or DEFAULT_URL


def default_model() -> str | None:
    """--ollama-model fallback: TLWU_OLLAMA_MODEL, else None (= the first model installed in Ollama)."""
    return os.environ.get("TLWU_OLLAMA_MODEL") or None


def _endpoint(url: str) -> tuple[str, int]:
    parts = urlsplit(url if "//" in url else f"//{url}")
    host = parts.hostname or ""
    if not host or not is_loopback_host(host):
        raise ValueError(f"--ollama-url must be a local address (127.0.0.1 / localhost), got '{host}'")
    return host, parts.port or 11434


class OllamaTranslator(Translator):
    """Direct src -> tgt translation with a model served by local Ollama; OpenCC afterwards when tgt is zh."""

    def __init__(self, model: str | None = None, url: str | None = None, zh_variant: str = "tw",
                 transport=None):
        self.model = model or default_model()
        self.url = url or default_url()
        self.host, self.port = _endpoint(self.url)
        self.zh_variant = zh_variant
        self._transport = transport or self._http  # (method, path, body|None) -> dict; injectable for tests
        self.history: deque = deque(maxlen=CONTEXT_LINES)
        self._pair: tuple[str, str] | None = None
        self.glossary = None  # set by the CLI, same contract as the other LLM engines

    def _http(self, method: str, path: str, body: dict | None = None) -> dict:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=TIMEOUT_S)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None,
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read().decode("utf-8", "replace")
            if resp.status != 200:
                raise RuntimeError(f"Ollama HTTP {resp.status}: {data[:200]}")
            return json.loads(data)
        except OSError as e:
            raise RuntimeError(f"cannot reach Ollama at {self.url} ({e}). Start it with `ollama serve`.") from e
        finally:
            conn.close()

    def preload(self, src: str = "", tgt: str = "") -> None:
        """Fail early with a clear message: server down or model not pulled."""
        installed = [m.get("name") for m in self._transport("GET", "/api/tags", None).get("models", [])
                     if m.get("name")]
        if self.model is None:  # no --ollama-model: use what the user already pulled, never pull anything
            if not installed:
                raise RuntimeError("Ollama has no model installed. Pull one yourself (`ollama pull <model>`), "
                                   "then pass it with --ollama-model.")
            self.model = sorted(installed)[0]
            print(f"tlwu: ollama model = {self.model} (first installed; choose one with --ollama-model)",
                  file=sys.stderr)
            return
        names = set(installed)
        wanted = {self.model, self.model if ":" in self.model else f"{self.model}:latest"}
        if not names & wanted:
            raise RuntimeError(f"Ollama model '{self.model}' is not installed. Run `ollama pull {self.model}`.")

    def draft(self, text: str, src: str, tgt: str) -> Translation:
        return self.translate(text, src, tgt, draft=True)

    def translate(self, text: str, src: str, tgt: str, draft: bool = False) -> Translation:
        if src == tgt or not text.strip():
            return Translation(None, text)
        text = collapse_repeats(text)
        if self.model is None:
            self.preload(src, tgt)
        if self._pair != (src, tgt):
            if draft:  # drafts never change state: a new pair just gets no context
                return self._translate(text, src, tgt, draft, ())
            self.history.clear()
            self._pair = (src, tgt)
        return self._translate(text, src, tgt, draft, trim_context(self.history))

    def _translate(self, text: str, src: str, tgt: str, draft: bool, ctx) -> Translation:
        note = self.glossary.prompt_block(text, *(a for a, _ in ctx)) if self.glossary else ""
        budget = max_tokens_for(text)
        body = {
            "model": self.model,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "messages": [
                {"role": "system", "content": build_system_prompt(src, tgt, self.zh_variant)},
                {"role": "user", "content": build_user(text, ctx, note)},
            ],
            "options": {"temperature": 0, "num_predict": min(budget, DRAFT_MAX_TOKENS) if draft else budget},
        }
        reply = self._transport("POST", "/api/chat", body)
        out = clean_output((reply.get("message") or {}).get("content", ""))
        out = convert_output(out, tgt, self.zh_variant)
        if out and not draft:
            self.history.append((text.strip(), out))
        return Translation(None, out)
