/// Pure sentence-segmentation helpers (no Flutter / FFI imports, unit-testable).
///
/// The Vosk ru streaming model emits lowercase text without punctuation, so
/// real boundaries come from the recognizer endpoint. These helpers handle the
/// remaining cases: punctuation (if a model ever emits it) and over-long
/// utterances.
library;

class SentenceSegmenter {
  /// Collapses whitespace runs and trims.
  static String normalize(String text) =>
      text.replaceAll(RegExp(r'\s+'), ' ').trim();

  static int wordCount(String text) {
    final t = normalize(text);
    return t.isEmpty ? 0 : t.split(' ').length;
  }

  /// Splits [text] into sentences. Splits after . ! ? … (followed by space or
  /// end of string), then breaks any sentence longer than [maxWords] into
  /// chunks of at most [maxWords] words, preferring a comma boundary.
  static List<String> split(String text, {int maxWords = 24}) {
    assert(maxWords > 0);
    final t = normalize(text);
    if (t.isEmpty) return const [];
    const enders = '.!?…';
    final raw = <String>[];
    final buf = StringBuffer();
    for (var i = 0; i < t.length; i++) {
      final ch = t[i];
      buf.write(ch);
      if (enders.contains(ch)) {
        final atEnd = i == t.length - 1;
        final nextIsSpace = !atEnd && t[i + 1] == ' ';
        final nextIsPunct = !atEnd && enders.contains(t[i + 1]);
        if ((atEnd || nextIsSpace) && !nextIsPunct) {
          raw.add(buf.toString().trim());
          buf.clear();
        }
      }
    }
    if (buf.toString().trim().isNotEmpty) raw.add(buf.toString().trim());

    final out = <String>[];
    for (final s in raw) {
      out.addAll(_chunkLong(s, maxWords));
    }
    return out
        .where((s) => s.replaceAll(RegExp(r'[\s.!?…,]'), '').isNotEmpty)
        .toList();
  }

  static List<String> _chunkLong(String sentence, int maxWords) {
    final words = sentence.split(' ');
    if (words.length <= maxWords) return [sentence];
    final chunks = <String>[];
    var start = 0;
    while (words.length - start > maxWords) {
      var end = start + maxWords;
      // Prefer the last comma-terminated word in the back half of the window.
      for (var j = end - 1; j >= start + maxWords ~/ 2; j--) {
        if (words[j].endsWith(',')) {
          end = j + 1;
          break;
        }
      }
      chunks.add(words.sublist(start, end).join(' '));
      start = end;
    }
    chunks.add(words.sublist(start).join(' '));
    return chunks;
  }

  /// True if a still-growing partial should be force-committed even though the
  /// recognizer has not signalled an endpoint (very long unbroken speech).
  static bool shouldForceCommit(String partial, {int maxWords = 24}) =>
      wordCount(partial) >= maxWords;
}
