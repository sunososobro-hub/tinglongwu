/// Prompt building and output cleaning for the Qwen3 translator (pure logic).
library;

class ContextPair {
  const ContextPair(this.ru, this.zh);
  final String ru;
  final String zh;
}

class PromptBuilder {
  static const system =
      'You are a live-subtitle translator. Translate Russian speech transcripts '
      'into Traditional Chinese (繁體中文, Taiwan usage). The transcript has no '
      'punctuation and may contain recognition errors; infer the intended '
      'meaning. Output ONLY the Chinese translation on one line. Never output '
      'Simplified Chinese, Russian, pinyin or explanations.';

  /// Extra reminder used for the retry when the first output was not
  /// Traditional Chinese.
  static const strictSuffix =
      '\n(IMPORTANT: answer strictly in Traditional Chinese characters 繁體字, '
      'e.g. 這、們、說、時、國; never 这、们、说、时、国.)';

  /// How many previous sentences are given as context.
  static const contextSentences = 2;

  /// Builds a Qwen3 ChatML prompt with thinking disabled (empty think block
  /// plus the /no_think soft switch).
  static String build(
    String ru, {
    List<ContextPair> context = const [],
    bool strict = false,
  }) {
    final ctx = context.length > contextSentences
        ? context.sublist(context.length - contextSentences)
        : context;
    final b = StringBuffer()
      ..write('<|im_start|>system\n')
      ..write(system)
      ..write('<|im_end|>\n')
      ..write('<|im_start|>user\n');
    if (ctx.isNotEmpty) {
      b.write('Previous sentences (context only, do not translate again):\n');
      for (final c in ctx) {
        b.write('RU: ${c.ru}\nZH: ${c.zh}\n');
      }
      b.write('\n');
    }
    b.write('Translate to Traditional Chinese:\n${ru.trim()}');
    if (strict) b.write(strictSuffix);
    b
      ..write(' /no_think<|im_end|>\n')
      ..write('<|im_start|>assistant\n')
      ..write('<think>\n\n</think>\n\n');
    return b.toString();
  }

  static final RegExp _label = RegExp(
    r'^(ZH|翻譯|翻译|譯文|Translation)\s*[:：]\s*',
    caseSensitive: false,
  );

  /// Strips think blocks, ChatML tokens, labels and wrapping quotes from raw
  /// model output; keeps only the first non-empty line.
  static String clean(String raw) {
    var t = raw;
    t = t.replaceAll(RegExp(r'<think>[\s\S]*?</think>'), '');
    t = t.replaceAll(RegExp(r'</?think>'), '');
    t = t.replaceAll(RegExp(r'<\|im_(start|end)\|>'), '');
    t = t.trim();
    final lines = t.split('\n').map((l) => l.trim()).where((l) => l.isNotEmpty);
    if (lines.isEmpty) return '';
    t = lines.first.replaceFirst(_label, '');
    const pairs = <String, String>{
      '"': '"',
      '“': '”',
      '「': '」',
      '『': '』',
    };
    if (t.length >= 2 && pairs[t[0]] == t[t.length - 1]) {
      t = t.substring(1, t.length - 1).trim();
    }
    return t;
  }
}
