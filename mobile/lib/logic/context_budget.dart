/// Keeps every Hy-MT2 request inside the llama.cpp context (pure logic, #533).
///
/// The pinned llama_cpp_dart runs with context shifting off, so a request
/// whose prompt tokens plus generated tokens reach `nCtx` throws "context full"
/// and stops the app. The binding has no public token-count call, so the budget
/// uses a char-based upper estimate of the prompt tokens.
///
/// Numbers measured with the Hy-MT2-1.8B-Q4_K_M GGUF tokenizer (llama.cpp,
/// `PromptBuilder.build` prompt): template overhead 18 tokens; ordinary
/// Russian 0.35-0.45 tokens/char; abbreviation runs 0.72; random lowercase
/// Cyrillic (worst case for ASR output) 0.84. The estimate below uses
/// 1 token/char and a 32-token overhead, which is above all of these.
library;

import 'prompt.dart';

class ContextBudget {
  /// KV context of the translator (`Translator.load`).
  static const nCtx = 768;

  /// Upper estimate of the prompt template tokens (measured 18).
  static const promptOverheadTokens = 32;

  /// Longest merged source text: overhead + 1 token/char + the largest
  /// `maxTokensFor` (400) still fits in `nCtx - 1` (32 + 320 + 400 = 752).
  static const maxMergeChars = 320;

  /// Upper estimate of the prompt tokens for [text].
  static int promptTokens(String text) =>
      promptOverheadTokens + text.trim().length;

  /// `PromptBuilder.maxTokensFor`, clamped so the prompt estimate plus the
  /// generated tokens stay below [nCtx] (at least 1 token).
  static int maxTokens(String text, {int nCtx = nCtx}) {
    final want = PromptBuilder.maxTokensFor(text.trim());
    final room = nCtx - 1 - promptTokens(text);
    if (want <= room) return want;
    return room < 1 ? 1 : room;
  }

  /// Text of two queued jobs merged into one request.
  static String join(String a, String b) => '$a $b';

  /// Index `i` of the oldest adjacent pair `texts[i]`, `texts[i + 1]` whose
  /// merged text is at most [maxChars] long, or null when no pair fits (then
  /// the queue is left longer instead of building a request that overflows).
  ///
  /// When [langs] is given (same length as [texts]), only pairs with the same
  /// language qualify, so a zh/en boundary does not block a later same-language
  /// pair (#625).
  static int? mergeIndex(List<String> texts,
      {int maxChars = maxMergeChars, List<String>? langs}) {
    for (var i = 0; i + 1 < texts.length; i++) {
      if (langs != null && langs[i] != langs[i + 1]) continue;
      if (join(texts[i], texts[i + 1]).length <= maxChars) return i;
    }
    return null;
  }

  /// Plan the queue trim of `Pipeline._onFinal` (#647): repeatedly merge the
  /// oldest fitting same-language pair until at most [maxQueue] jobs are left,
  /// or no pair fits. Returns the merge indices in order; each index refers to
  /// the list as it is after the previous merges, plus the resulting texts and
  /// languages (a merged job keeps the language of its pair).
  static TrimPlan trimPlan(List<String> texts, List<String> langs, int maxQueue,
      {int maxChars = maxMergeChars}) {
    assert(texts.length == langs.length);
    final t = List<String>.of(texts);
    final l = List<String>.of(langs);
    final steps = <int>[];
    while (t.length > maxQueue) {
      final i = mergeIndex(t, maxChars: maxChars, langs: l);
      if (i == null) break;
      t[i] = join(t[i], t[i + 1]);
      t.removeAt(i + 1);
      l.removeAt(i + 1);
      steps.add(i);
    }
    return TrimPlan(steps, t, l);
  }
}

/// Result of [ContextBudget.trimPlan].
class TrimPlan {
  const TrimPlan(this.steps, this.texts, this.langs);
  final List<int> steps;
  final List<String> texts;
  final List<String> langs;
}
