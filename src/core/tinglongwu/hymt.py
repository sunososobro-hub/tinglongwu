"""Tencent Hy-MT2-1.8B (Apache-2.0, Q4_K_M GGUF, llama.cpp): direct multilingual MT.

It is the only built-in translation engine (#276, #471: the OPUS/Argos pivot is gone from the listen path):
`JaRouter(None, ..., all_pairs=True)` translates every pair whose two languages it knows (`EN_NAMES`) direct
src -> tgt, and any other pair is an error with the reason (no silent engine switch). The reverse/speak side
builds the same router with its own no-fallback base (#331).
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
from collections import deque
from pathlib import Path

from .engines import clean_output, max_tokens_for
from .engines import load_llama as _load_llama
from .engines import shared_llama
from .llm_prompt import CONTEXT_LINES
from .repeat import collapse_repeats
from .translate import Translation, Translator, strip_unk, zh_convert

HYMT_DIR = "hymt2-1.8b"
HYMT_FILE = "Hy-MT2-1.8B-Q4_K_M.gguf"
HYMT_REPO = "tencent/Hy-MT2-1.8B-GGUF"
# #279: opt-in Hy-MT2 7B Q4_K_M (~4.8 GB, GPUs with >= 8 GB); same family, prompt and sampling as 1.8B.
# Never chosen by auto: only `--engine hymt7b` after `tlwu-setup --hymt7b`.
HYMT_SIZES = {"1.8b": ("hymt2-1.8b", HYMT_FILE, "--hymt"), "7b": ("hymt2-7b", "Hy-MT2-7B-Q4_K_M.gguf", "--hymt7b")}
# Model card sampling for the 1.8B model, except temperature: #107 measured 0.7 / 0.2 / 0 on the full FLORES-200
# devtest (rus/eng/jpn -> zho_Hant, eval-flores run 43554407306): COMET within 0.0006, chrF within 0.2, no U+FFFD
# at any setting, so tlwu ships greedy (0): no sampling randomness, the same prompt gives the same line.
SAMPLING = dict(temperature=0.0, top_p=0.6, top_k=20, repeat_penalty=1.05)



def sampling() -> dict:
    """SAMPLING, with temperature overridable by env TLWU_HYMT_TEMP (e.g. 0 for repeatable terminology)."""
    s = dict(SAMPLING)
    try:
        s["temperature"] = float(os.environ["TLWU_HYMT_TEMP"])
    except (KeyError, ValueError):
        pass
    return s


# #335: it/pt/vi/th/id are on the Hy-MT2 model card's language list, so they go direct instead of the OPUS pivot.
# #942: hi (Hindi, 印地语 on the model card) too; #620 measured direct hi->zh with the same 1.8B (run 43521109509).
ZH_NAMES = {"zh": "中文", "ja": "日语", "en": "英语", "ru": "俄语", "ko": "韩语", "de": "德语", "fr": "法语",
            "es": "西班牙语", "it": "意大利语", "pt": "葡萄牙语", "vi": "越南语", "th": "泰语", "id": "印尼语",
            "hi": "印地语"}
EN_NAMES = {"zh": "Chinese", "ja": "Japanese", "en": "English", "ru": "Russian", "ko": "Korean",
            "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
            "vi": "Vietnamese", "th": "Thai", "id": "Indonesian", "hi": "Hindi"}


def hymt_path(models_dir: Path, size: str = "1.8b") -> Path:
    d, f, _ = HYMT_SIZES[size]
    return Path(models_dir) / d / f


def setup_flag(size: str = "1.8b") -> str:
    """The tlwu-setup flag that downloads this size."""
    return HYMT_SIZES[size][2]


def build_prompt(text: str, src: str, tgt: str, terms: list[tuple[str, str]] | None = None,
                 context: str | None = None) -> str:
    """Model-card template; the Chinese one when Chinese is on either side, else the English one.
    `terms` (source, translation) pairs are prepended in the card's terminology-reference form; none = unchanged.
    `context` (previous source sentence) uses the card's contextual-translation form (#1258 eval); none = unchanged."""
    if terms:
        ref = "\n".join(f"{a} 翻译成 {b}" for a, b in terms)
        return f"参考下面的翻译：\n{ref}\n\n" + build_prompt(text, src, tgt, context=context)
    if context and context.strip():
        return (f"{context.strip()}\n参考上面的信息，把下面的文本翻译成{ZH_NAMES.get(tgt, tgt)}，"
                f"注意不需要翻译上文，也不要额外解释：\n{text}")
    if "zh" in (src, tgt):
        return (f"将以下文本翻译成{ZH_NAMES.get(tgt, tgt)}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}")
    return (f"Translate the following text into {EN_NAMES.get(tgt, tgt)}.\n"
            f"Note that you should only output the translated result without any additional explanation:\n\n{text}")


# #1351: Hy-MT2 sometimes starts its answer with the card prompt's own instruction line (eval-flores run 44013625413,
# ru->en: "Note that you should only output the translated result without any additional explanation: <translation>",
# also reworded as "It should be noted that only the translated result should be output, ..."). Only a leading echo
# that names the "translated result" / "额外解释" instruction is removed; a translation that merely says "note" stays.
# one sentence (no "." / ":" inside) that says to output only the translated result/text, with no explanation
_ECHO_EN = re.compile(r"^\s*(?:(?:please\s+)?note(?:\s+that)?|it\s+should\s+be\s+noted\s+that|you\s+should)[,:]?\s*"
                      r"(?=[^.:\n]*\boutput\b)"
                      r"[^.:\n]{0,40}?\bonly\b[^.:\n]{0,40}?\btranslated\s+(?:result|text)\b"
                      r"[^.:\n]{0,40}?\b(?:without|no)\b[^.:\n]{0,40}?\bexplanations?\s*(?:[.:]|(?=\n)|$)\s*", re.I)
# the contextual form's instruction (build_prompt with `context`, #1258 eval only)
_ECHO_ZH_CTX = re.compile(r"^\s*参考上面的信息[，,]\s*把下面的文本翻译成[^，,：:\n]{1,12}[，,]\s*注意不需要翻译上文[，,]\s*"
                          r"也不要额外解释[。：:]\s*")
_ECHO_ZH = re.compile(r"^\s*(?:注意)?[，,：:]?\s*只需要?[输輸]出翻[译譯][^。：:\n]{0,12}?(?:额外|額外)(?:解释|解釋)(?:[。：:]|(?=\n)|$)\s*")


# #1615: the same instruction rendered in the TARGET language (phone field test, auto -> ru: "Примите во внимание,
# что следует выводить только переведенный результат, без каких-либо дополнительных комментариев"). A leading sentence
# (up to . : ! ? or a line break) is an echo only when it holds all five parts of the instruction in one language:
# only / translat- / result|text / without / explanation|comment. Kept in sync with mobile `PromptBuilder.stripEcho`.
ECHO_WORDS = (
    ("только", "переведённ|переведенн|перевод(?!чи)", "результат|текст", "без", "коммент|объясн|пояснен"),  # ru
    (r"\bnur\b", "übersetz", "ergebnis|text", r"\bohne\b|\bkeine", "erklär|erläuter|kommentar"),  # de
    ("uniquement|seulement|ne .{0,40}que", "tradu", "résultat|texte", r"\bsans\b|aucun", "explication|commentaire"),  # fr
    ("solo|sólo|únicamente|solamente", "traduc", "resultado|texto", r"\bsin\b|ningún|ninguna",
     "explicaci|comentario"),  # es
    ("solo|soltanto|unicamente|solamente", "tradu", "risultato|testo", "senza|nessun", "spiegazion|comment"),  # it
    ("apenas|somente", "tradu", "resultado|texto", r"\bsem\b|nenhum", "explicaç|explicac|comentário"),  # pt
    ("のみ|だけ", "翻訳", "結果|テキスト|訳文", "説明|解説", "不要|せず|しない|なし|ないで"),  # ja
    ("만", "번역", "결과|텍스트", "설명", "없이|말고|하지"),  # ko
    ("chỉ", "dịch", "kết quả|văn bản", "không", "giải thích"),  # vi
    ("hanya", "terjemah", "hasil|teks", "tanpa", "penjelasan"),  # id
    ("เท่านั้น", "แปล", "ผล|ข้อความ", "ไม่", "อธิบาย"),  # th
)
_ECHO_WORDS_RX = tuple(tuple(re.compile(p) for p in row) for row in ECHO_WORDS)
# a sentence end, or a ":" that may close a lead-in ("Обратите внимание: ...") or the echo itself
_ECHO_END = re.compile(r"[.:!?。：！？]+\s*|\n\s*|\Z")


def _strip_translated_echo(out: str) -> str:
    s = out.lstrip()
    low = s.lower()
    for m in _ECHO_END.finditer(s):
        if m.start() > 240:
            break
        if m.start() and any(all(rx.search(low[:m.start()]) for rx in row) for row in _ECHO_WORDS_RX):
            return s[m.end():]
        if m.group().strip(" \t:："):  # a real sentence end (or a line break): the echo is one sentence
            break
    return out


def strip_echo(out: str, translated: bool = True) -> str:
    """Drop the card instruction line when the model repeats it before (or instead of) the translation,
    in English, Chinese or (#1615, `translated`) translated into the target language. Run it on the raw
    model text: clean_output joins lines, and an unpunctuated echo would then run into the translation."""
    for rx in (_ECHO_EN, _ECHO_ZH, _ECHO_ZH_CTX):
        out = rx.sub("", out, count=1)
    return (_strip_translated_echo(out) if translated else out).strip()


def zh_script_guard(src_text: str, out: str, max_words: int = 4) -> str:
    """Short line whose "Chinese" output still contains kana / Cyrillic / Greek (left untranslated or echoed):
    show the source text instead. Same size limits as translate.zh_guard, but only the wrong-script test: its
    rare-ideograph test is tuned for OPUS garbage and would reject valid Taiwan interjections (欸, 齁)."""
    if len(src_text.split()) <= max_words and len(src_text) <= 40 and any(
            "\u0370" <= c <= "\u03ff" or "\u0400" <= c <= "\u04ff" or "\u3040" <= c <= "\u30ff" for c in out):
        return src_text
    return out


class HyMtTranslator(Translator):
    """Direct src -> tgt with a local Hy-MT2 GGUF; OpenCC afterwards when tgt is zh. The prompt is the card's
    single-message template (no context lines); `history` keeps the previous CONTEXT_LINES (source, translation)
    pairs of the current language pair only to pick glossary entries, like the other LLM engines (#535)."""

    def __init__(self, models_dir: Path, zh_variant: str = "tw", device: str = "cpu", loader=None,
                 size: str = "1.8b"):
        from .models import require

        self.zh_variant, self.device, self.size = zh_variant, device, size
        self.path = require(hymt_path(models_dir, size), f"Hy-MT2 {size.upper()} translation model", setup_flag(size))
        self._loader = loader or _load_llama
        self.llm = None
        self.history: deque = deque(maxlen=CONTEXT_LINES)
        self._pair: tuple[str, str] | None = None
        self.glossary = None  # tinglongwu.glossary.Glossary (set by the CLI): `~` fixes on the zh output (#174)
        self._zh_words: dict[str, str] = {}
        self.on_partial = None  # #1771: callback(text) for a finished line's translation so far (see streaming())

    def _zh_word(self, w: str) -> str:
        if w not in self._zh_words:
            self._zh_words[w] = zh_convert(w, self.zh_variant)
        return self._zh_words[w]

    def _terms(self, text: str, tgt: str, context=()) -> list[tuple[str, str]]:
        """Glossary entries occurring in the sentence or the previous `context` source lines, as (source, zh
        translation); only for zh output. They enter the prompt through build_prompt's terminology form."""
        if tgt != "zh" or not self.glossary:
            return []
        out = []
        for e in self.glossary.relevant(text, *context):
            if e.target:
                out.append((e.main, self._zh_word(e.target)))
            elif e.main.isascii():  # plain hint term (#1328): Latin-script ones are kept as written; others never forced into zh
                out.append((e.main, e.main))
        return out

    def _load(self):
        if self.llm is None:
            self.llm = shared_llama(self.path, self.device == "cuda", self._loader)  # one instance per process (#285)

    def preload(self, src: str = "", tgt: str = "") -> None:
        self._load()

    takes_context = True  # #1750 --mt-context

    def translate(self, text: str, src: str, tgt: str, draft: bool = False, context: str | None = None) -> Translation:
        """`draft` (streaming partials): uses the context but never adds to it. `context`: previous source sentence
        put into the prompt (card's contextual form); the #1258 eval and live --mt-context (#1750) pass it."""
        if src == tgt or not text.strip():
            return Translation(None, text)
        text = collapse_repeats(text)
        self._load()
        if self._pair != (src, tgt):
            ctx = []
            if not draft:
                self.history.clear()
                self._pair = (src, tgt)
        else:
            ctx = [a for a, _ in self.history]
        msgs = [{"role": "user", "content": build_prompt(text, src, tgt, self._terms(text, tgt, ctx), context)}]
        cb = None if draft else self.on_partial
        if cb is None:
            r = self.llm.create_chat_completion(msgs, max_tokens=max_tokens_for(text), **sampling())
            content = r["choices"][0]["message"]["content"] or ""
        else:  # #1771: the same request, token by token; the finished text below is built exactly as unstreamed
            content = self._stream(msgs, max_tokens_for(text), tgt, cb)
        raw = strip_echo(content)  # before clean_output joins the lines (#1615)
        out = strip_echo(strip_unk(clean_output(raw)), translated=False)
        if tgt == "zh":
            out = zh_convert(out, self.zh_variant)
            if self.glossary:
                out = self.glossary.fix_output(out, text, convert=self._zh_word)
            out = zh_script_guard(text, out)
        if not draft:
            self.history.append((text.strip(), out))
        return Translation(None, out)

    def _stream(self, msgs, max_tokens: int, tgt: str, cb) -> str:
        """#1771: run the completion streamed; cb(partial) after each token that changes the shown text."""
        llm = self.llm
        it = (llm.stream_chat_completion(msgs, max_tokens=max_tokens, **sampling())
              if hasattr(type(llm), "stream_chat_completion")
              else llm.create_chat_completion(msgs, max_tokens=max_tokens, stream=True, **sampling()))
        acc, shown = "", ""
        try:
            for ch in it:
                piece = ((ch.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if not piece:
                    continue
                acc += piece
                part = partial_text(acc, tgt, self.zh_variant)
                if part and part != shown:
                    shown = part
                    try:
                        cb(part)
                    except Exception:  # noqa: BLE001 - a display callback never breaks the translation
                        pass
        finally:
            close = getattr(it, "close", None)
            if close is not None:
                close()  # releases SharedLlama's lock even if the loop above raised
        return acc


# #1771: a streamed answer that starts like the card instruction may be an echo (strip_echo drops it only once it is
# complete), so it is not shown as a partial until strip_echo has removed it; the finished line is unaffected.
_ECHO_LEADS = ("note", "please note", "it should", "you should", "注意", "只需", "只需要", "参考上面", "将以下")


def partial_text(acc: str, tgt: str, zh_variant: str = "tw") -> str:
    """#1771: what a subtitle shows for the streamed model text `acc` so far ("" = nothing yet). A display hint only:
    the final line goes through the full translate() clean-up (glossary, script guard, per-language fixes)."""
    s = strip_echo(acc, translated=False)
    head = acc.lstrip().lower()[:16]
    if s == acc.strip() and any(head.startswith(w) or w.startswith(head) for w in _ECHO_LEADS):
        return ""  # may still be the instruction echo
    s = strip_unk(clean_output(s)).strip()
    if s and tgt == "zh":
        s = zh_convert(s, zh_variant)
    return s


def stream_engine(translator):
    """#1771: the HyMtTranslator a final line of `translator` would stream from (through a PendingTranslator and a
    JaRouter), or None (another engine, or Hy-MT2 not loaded / failed)."""
    t = translator
    for _ in range(3):
        if isinstance(t, HyMtTranslator):
            return t
        inner = t.__dict__.get("_inner") if hasattr(t, "__dict__") else None  # PendingTranslator (no __getattr__)
        t = inner if inner is not None else (t.hy if isinstance(t, JaRouter) else None)
        if t is None:
            return None
    return t if isinstance(t, HyMtTranslator) else None


@contextlib.contextmanager
def streaming(translator, cb):
    """#1771: while inside, finished (non-draft) translations of `translator` call cb(partial text) per token.
    cb None, or an engine that cannot stream: no-op. Only the MT worker thread uses it (one line at a time)."""
    hy = stream_engine(translator) if cb is not None else None
    if hy is None:
        yield
        return
    hy.on_partial = cb
    try:
        yield
    finally:
        hy.on_partial = None


class JaRouter(Translator):
    """Pairs with Japanese -> Hy-MT2 (direct); every other pair -> `base`. If Hy-MT2 is not installed (or fails
    to load) ja -> X falls back to base's pivot; X -> ja then raises (there is no OPUS route to Japanese).

    all_pairs=True (Hy-MT2 as the main engine, #66): every pair whose two languages Hy-MT2 knows goes direct
    through it; a pair with an unknown language, or any pair after Hy-MT2 failed, goes to `base`.
    base=None (the listen path since #471: Hy-MT2 is the only engine, no OPUS pivot): such a pair raises
    RuntimeError with the reason instead of switching engines."""

    def __init__(self, base: Translator | None, models_dir: Path, zh_variant: str = "tw", device: str = "cpu",
                 factory=HyMtTranslator, warn=lambda m: print(m, file=sys.stderr), all_pairs: bool = False,
                 size: str = "1.8b"):
        self.base = base if base is not None else _NoFallback(self)
        self.zh_variant, self.all_pairs = zh_variant, all_pairs
        self._pivot = base is not None
        self.history: list = []
        self._pair = None
        self._warn = warn
        self.hy = None
        self.hy_error = ""
        self._glossary = None
        if hymt_path(models_dir, size).exists():
            try:  # the 1.8B call keeps the old 3-argument factory signature
                self.hy = (factory(models_dir, zh_variant, device) if size == "1.8b"
                           else factory(models_dir, zh_variant, device, size=size))
            except Exception as e:  # noqa: BLE001
                self.hy_error = str(e)
        else:
            self.hy_error = f"{hymt_path(models_dir, size)} missing (run `tlwu-setup {setup_flag(size)}`)"

    @property
    def glossary(self):
        return self._glossary

    @glossary.setter
    def glossary(self, g) -> None:
        """One glossary for both routes (#174): Hy-MT2 and the base (if any) both apply its `~` fixes."""
        self._glossary = g
        for t in (self.hy, self.base):
            if t is not None and hasattr(t, "glossary"):
                t.glossary = g

    def _use_hy(self, src: str, tgt: str) -> bool:
        if self.hy is None:
            return False
        if self.all_pairs:
            return src in EN_NAMES and tgt in EN_NAMES
        return "ja" in (src, tgt)

    def _guard(self, src: str, tgt: str) -> None:
        if tgt == "ja" and self.hy is None:
            raise RuntimeError(f"Japanese output needs the Hy-MT2 model: {self.hy_error}")

    def _fallback(self, e: Exception) -> None:
        what = "translation" if self.all_pairs else "ja -> X"
        if self._pivot:
            self._warn(f"tlwu: WARNING: Hy-MT2 failed ({str(e)[:160]}); {what} falls back to the OPUS pivot "
                       "(lower translation quality).")
        else:
            self._warn(f"tlwu: WARNING: Hy-MT2 failed ({str(e)[:160]}); there is no fallback engine (#471).")
        self.hy_error, self.hy = str(e), None

    def preload(self, src: str, tgt: str) -> None:
        self._guard(src, tgt)
        if self._use_hy(src, tgt):
            try:
                self.hy.preload()
                return
            except Exception as e:  # noqa: BLE001
                if tgt == "ja":
                    raise
                self._fallback(e)
        elif "ja" in (src, tgt) and self._pivot:
            self._warn(f"tlwu: Hy-MT2 unavailable for Japanese ({self.hy_error}); using OPUS pivot ja -> en -> zh.")
        self.base.preload(src, tgt)

    takes_context = True  # #1750: context reaches Hy-MT2 only; the pivot / no-fallback base ignores it

    def translate(self, text: str, src: str, tgt: str, draft: bool = False, context: str | None = None) -> Translation:
        self._guard(src, tgt)
        if self._use_hy(src, tgt):
            try:
                if draft:
                    return self.hy.translate(text, src, tgt, draft=True)
                return self.hy.translate(text, src, tgt, context=context) if context else self.hy.translate(text, src, tgt)
            except Exception as e:  # noqa: BLE001
                if tgt == "ja":
                    raise
                self._fallback(e)
        return self.base.translate(text, src, tgt)

    def draft(self, text: str, src: str, tgt: str) -> Translation:
        return self.translate(text, src, tgt, draft=True)


class _NoFallback(Translator):
    """JaRouter's base when there is no pivot (#471): every pair Hy-MT2 does not take is an error with the reason."""

    def __init__(self, router: JaRouter):
        self.router = router
        self.glossary = None

    def _fail(self, src: str, tgt: str):
        """A language Hy-MT2 does not know (e.g. one auto-detected by Whisper) raises ModelNotFound, which the
        pipeline reports once per pair and keeps running (as the OPUS pivot's missing pair did). A Hy-MT2 that is
        gone (failed to load / crashed mid-session) is a RuntimeError that stops the session with the reason."""
        r = self.router
        if r.hy is None:
            raise RuntimeError(f"Hy-MT2 is not available ({r.hy_error[:160]}); there is no fallback engine (#471). "
                               "Install or repair it with `tlwu-setup --hymt`")
        from .models import ModelNotFound

        raise ModelNotFound(f"Hy-MT2 does not translate {src} -> {tgt}; there is no fallback engine (#471)")

    def preload(self, src: str, tgt: str) -> None:
        self._fail(src, tgt)

    def translate(self, text: str, src: str, tgt: str) -> Translation:
        self._fail(src, tgt)


def active_path(translator) -> tuple[str, int] | None:
    """#1198: (backend, decode threads) of the Hy-MT2 model `translator` uses (HyMtTranslator or JaRouter), None
    for other engines or before the model is loaded."""
    from .mt_backend import path_of

    hy = getattr(translator, "hy", translator) if isinstance(translator, JaRouter) else translator
    if not isinstance(hy, HyMtTranslator) or hy.llm is None:
        return None
    return path_of(hy.llm)
