/// Drops background-chatter finals in conversation mode (#1479). Pure Dart, no Flutter imports.
///
/// Rule: a final is dropped when it has at most [maxFillerWords] words and EVERY word is a known
/// filler / interjection (per-language list below). Short real answers ("нет", "yes", "好") are
/// not in the lists, so they are kept. Dropped finals make no bubble and no LLM call.
library;

class FillerFilter {
  /// A final with more words than this is never dropped.
  static const maxFillerWords = 2;

  /// Filler / interjection words, lowercase, no punctuation. Real answers (yes / no / ok) are
  /// deliberately absent (ru ага/угу, ja ええ, ko 어 are affirmative answers too, #1484).
  static const fillers = <String>{
    // ru
    'и', 'а', 'ну', 'вот', 'там', 'э', 'эм', 'ээ', 'мм', 'ммм', 'так', 'ой',
    'ах', 'ох', 'ёмаё',
    // en
    'uh', 'um', 'umm', 'uhh', 'er', 'erm', 'hm', 'hmm', 'mm', 'mmm', 'ah', 'oh', 'huh', 'so',
    'well', 'like', 'the', 'a', 'and',
    // zh
    '嗯', '嗯嗯', '啊', '呃', '哦', '噢', '唉', '喔', '哈', '呀', '哎', '那個', '這個', '就是',
    // ja
    'えっと', 'あの', 'えー', 'あー', 'うーん', 'まあ', 'ん', 'はあ',
    // ko
    '음', '아', '그', '저', '흠', '뭐',
  };

  static final _strip = RegExp(r'[\p{P}\p{S}]+', unicode: true);

  static List<String> _words(String text) => text
      .toLowerCase()
      .replaceAll(_strip, ' ')
      .split(RegExp(r'\s+'))
      .where((w) => w.isNotEmpty)
      .toList();

  /// True when [text] should be dropped (no words at all also counts).
  static bool shouldDrop(String text) {
    final w = _words(text);
    if (w.isEmpty) return true;
    return w.length <= maxFillerWords && w.every(fillers.contains);
  }
}
