/// Target-script check for one translated line (#1616, pure logic).
///
/// Field test 2026-11-19 (phone, auto -> Русский): the zh line "聽少說嗎?" came back in English
/// ("Do you want to talk or not?") although the route and the prompt asked for Russian (src zh,
/// tgt ru, Chinese card template "将以下文本翻译成俄语"): Hy-MT2-1.8B drifted to English on a short
/// question. Greedy decoding repeats the same answer for the same prompt, so the retry uses the
/// other card template ([PromptBuilder.userMessage] `alt`), and a second wrong-script answer is
/// dropped (the line is shown untranslated, like an echo-only answer, #1615).
library;

typedef _Is = bool Function(int c);

bool _cyrillic(int c) => c >= 0x0400 && c <= 0x052F;
bool _han(int c) =>
    (c >= 0x3400 && c <= 0x4DBF) || (c >= 0x4E00 && c <= 0x9FFF) || (c >= 0xF900 && c <= 0xFAFF);
bool _kana(int c) => c >= 0x3040 && c <= 0x30FF;
bool _hangul(int c) =>
    (c >= 0xAC00 && c <= 0xD7AF) || (c >= 0x1100 && c <= 0x11FF) || (c >= 0x3130 && c <= 0x318F);
bool _thai(int c) => c >= 0x0E00 && c <= 0x0E7F;
bool _latin(int c) =>
    (c >= 0x41 && c <= 0x5A) ||
    (c >= 0x61 && c <= 0x7A) ||
    (c >= 0xC0 && c <= 0x24F && c != 0xD7 && c != 0xF7) ||
    (c >= 0x1E00 && c <= 0x1EFF);

/// Letters a line in each target language must contain (at least one of them).
final Map<String, List<_Is>> _targetScripts = {
  'zh': [_han],
  'yue': [_han],
  'ja': [_kana, _han],
  'ko': [_hangul],
  'ru': [_cyrillic],
  'th': [_thai],
  for (final l in ['en', 'es', 'fr', 'de', 'it', 'pt', 'vi', 'id']) l: [_latin],
};

final RegExp _letter = RegExp(r'\p{L}', unicode: true);
final RegExp _latinWord = RegExp(r'[A-Za-zÀ-ɏḀ-ỿ]+');

/// True when [out] (the cleaned translation of [src]) is in the wrong script for [tgt]: it has no
/// letter of the target script and at least [minForeign] letters of another script that were not
/// copied from the source (a name or "OK" kept as written is fine). Unknown targets and empty
/// output never count as wrong.
bool wrongScript(String src, String out, String tgt, {int minForeign = 2}) {
  final scripts = _targetScripts[tgt];
  if (scripts == null || out.trim().isEmpty) return false;
  final runes = out.runes.toList();
  if (runes.any((c) => scripts.any((s) => s(c)))) return false;
  // Latin words that also occur in the source (names, brands, "OK") are copies, not drift.
  final srcWords = {for (final m in _latinWord.allMatches(src.toLowerCase())) m.group(0)!};
  final kept = out.replaceAllMapped(
      _latinWord, (m) => srcWords.contains(m.group(0)!.toLowerCase()) ? '' : m.group(0)!);
  final srcChars = src.runes.toSet();
  var foreign = 0;
  for (final c in kept.runes) {
    // A non-Latin letter copied from the source (an untranslated name) is not drift either.
    if (!_latin(c) && srcChars.contains(c)) continue;
    if (_letter.hasMatch(String.fromCharCode(c))) foreign++;
  }
  return foreign >= minForeign;
}

/// Result of [translateInScript].
class ScriptChecked {
  const ScriptChecked(this.text, this.note);

  /// The translation to show ('' = shown untranslated).
  final String text;

  /// '' = first answer was fine; 'retry' = the alt template fixed it; 'dropped' = both wrong.
  final String note;
}

/// Runs one translation with the script check: [run] generates and cleans one answer
/// (`alt` = the other card template). A wrong-script first answer gets one retry with the other
/// template; a wrong-script second answer is dropped.
Future<ScriptChecked> translateInScript(String src, String tgt,
    Future<String> Function(bool alt) run) async {
  final first = await run(false);
  if (!wrongScript(src, first, tgt)) return ScriptChecked(first, '');
  final second = await run(true);
  if (!wrongScript(src, second, tgt)) return ScriptChecked(second, 'retry');
  return const ScriptChecked('', 'dropped');
}
