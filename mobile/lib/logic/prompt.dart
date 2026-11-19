/// Prompt building and output cleaning for the Hy-MT2-1.8B translator (pure logic).
///
/// Mirrors the desktop engine (`tinglongwu/hymt.py`): the user message is the
/// model-card template from `build_prompt`, wrapped in the chat template stored
/// in the GGUF (`tokenizer.chat_template`), which is what llama.cpp's
/// `create_chat_completion` renders on desktop. No context memory and no extra
/// system text: the card's single-message template.
library;

class PromptBuilder {
  /// Special tokens of the GGUF chat template (fullwidth bars, U+2581 spaces).
  static const bos = '<｜hy_begin▁of▁sentence｜>';
  static const userTag = '<｜hy_User｜>';
  static const assistantTag = '<｜hy_Assistant｜>';

  /// Same tables as desktop `hymt.ZH_NAMES` / `hymt.EN_NAMES`.
  static const zhNames = <String, String>{
    'zh': '中文', 'yue': '粤语', 'ja': '日语', 'en': '英语', 'ru': '俄语', 'ko': '韩语',
    'de': '德语', 'fr': '法语', 'es': '西班牙语', 'it': '意大利语',
    'pt': '葡萄牙语', 'vi': '越南语', 'th': '泰语', 'id': '印尼语',
  };
  static const enNames = <String, String>{
    'zh': 'Chinese', 'yue': 'Cantonese', 'ja': 'Japanese', 'en': 'English', 'ru': 'Russian',
    'ko': 'Korean', 'de': 'German', 'fr': 'French', 'es': 'Spanish',
    'it': 'Italian', 'pt': 'Portuguese', 'vi': 'Vietnamese', 'th': 'Thai',
    'id': 'Indonesian',
  };

  /// SenseVoice reports its language as `<|en|>`; returns `zh`/`en`/`ja`/`ko`/`yue`
  /// (anything else, or empty, falls back to `en`).
  static String fromSenseVoiceLang(String raw) {
    final c = raw.replaceAll(RegExp(r'[<>|\s]'), '').toLowerCase();
    const ok = {'zh', 'en', 'ja', 'ko', 'yue'};
    return ok.contains(c) ? c : 'en';
  }

  /// Desktop `hymt.build_prompt(text, src, tgt)` (no glossary terms): the
  /// Chinese card template when Chinese is on either side, else the English one.
  static String userMessage(String text, {String src = 'ru', String tgt = 'zh'}) {
    if (src == 'zh' || src == 'yue' || tgt == 'zh') {
      return '将以下文本翻译成${zhNames[tgt] ?? tgt}，'
          '注意只需要输出翻译后的结果，不要额外解释：\n\n$text';
    }
    return 'Translate the following text into ${enNames[tgt] ?? tgt}.\n'
        'Note that you should only output the translated result without any '
        'additional explanation:\n\n$text';
  }

  /// Full raw prompt for llama.cpp (tokenize with parseSpecial, no addSpecial):
  /// the GGUF chat template with one user message and add_generation_prompt.
  static String build(String text, {String src = 'ru', String tgt = 'zh'}) =>
      '$bos$userTag${userMessage(text, src: src, tgt: tgt)}$assistantTag';

  /// Desktop `engines.max_tokens_for`: output budget proportional to input.
  static int maxTokensFor(String text) {
    final n = 2 * text.length + 16;
    return n < 32 ? 32 : (n > 400 ? 400 : n);
  }

  /// Tighter output budget for the phone (#faithful): a Chinese line is much shorter than its
  /// Latin-script source, so cap at 0.7 tokens per source char (+12) instead of the desktop 2x; a
  /// model that wants to say more than that is inventing text. CJK sources keep the desktop budget
  /// (the English output is longer than the source). Never above [maxTokensFor].
  static int faithfulMaxTokens(String text, {String src = 'en', String tgt = 'zh'}) {
    final t = text.trim();
    final cap = maxTokensFor(t);
    if (tgt != 'zh' || src == 'zh' || src == 'yue' || src == 'ja' || src == 'ko') return cap;
    final tight = (t.length * 0.7).ceil() + 12;
    return tight < 24 ? 24 : (tight < cap ? tight : cap);
  }

  static final RegExp _label = RegExp(
    r'^(ZH|翻譯|翻译|譯文|译文|Translation)\s*[:：]\s*',
    caseSensitive: false,
  );

  // Desktop `hymt._ECHO_EN` / `_ECHO_ZH` (#1351): the card instruction repeated in English / Chinese.
  static final RegExp _echoEn = RegExp(
    r'^\s*(?:(?:please\s+)?note(?:\s+that)?|it\s+should\s+be\s+noted\s+that|you\s+should)[,:]?\s*'
    r'(?=[^.:\n]*\boutput\b)'
    r'[^.:\n]{0,40}?\bonly\b[^.:\n]{0,40}?\btranslated\s+(?:result|text)\b'
    r'[^.:\n]{0,40}?\b(?:without|no)\b[^.:\n]{0,40}?\bexplanations?\s*(?:[.:]|(?=\n)|$)\s*',
    caseSensitive: false,
  );
  static final RegExp _echoZh =
      RegExp(r'^\s*(?:注意)?[，,：:]?\s*只需要?[输輸]出翻[译譯][^。：:\n]{0,12}?(?:额外|額外)(?:解释|解釋)(?:[。：:]|(?=\n)|$)\s*');

  /// Desktop `hymt.ECHO_WORDS` (#1615): the instruction translated into the target language. A leading
  /// sentence is an echo only when it holds all five parts in one language:
  /// only / translat- / result|text / without / explanation|comment.
  static const echoWords = <List<String>>[
    ['только', 'переведённ|переведенн|перевод(?!чи)', 'результат|текст', 'без', 'коммент|объясн|пояснен'], // ru
    [r'\bnur\b', 'übersetz', 'ergebnis|text', r'\bohne\b|\bkeine', 'erklär|erläuter|kommentar'], // de
    ['uniquement|seulement|ne .{0,40}que', 'tradu', 'résultat|texte', r'\bsans\b|aucun', 'explication|commentaire'], // fr
    ['solo|sólo|únicamente|solamente', 'traduc', 'resultado|texto', r'\bsin\b|ningún|ninguna', 'explicaci|comentario'], // es
    ['solo|soltanto|unicamente|solamente', 'tradu', 'risultato|testo', 'senza|nessun', 'spiegazion|comment'], // it
    ['apenas|somente', 'tradu', 'resultado|texto', r'\bsem\b|nenhum', 'explicaç|explicac|comentário'], // pt
    ['のみ|だけ', '翻訳', '結果|テキスト|訳文', '説明|解説', '不要|せず|しない|なし|ないで'], // ja
    ['만', '번역', '결과|텍스트', '설명', '없이|말고|하지'], // ko
    ['chỉ', 'dịch', 'kết quả|văn bản', 'không', 'giải thích'], // vi
    ['hanya', 'terjemah', 'hasil|teks', 'tanpa', 'penjelasan'], // id
    ['เท่านั้น', 'แปล', 'ผล|ข้อความ', 'ไม่', 'อธิบาย'], // th
  ];
  static final List<List<RegExp>> _echoWordsRx =
      [for (final row in echoWords) [for (final p in row) RegExp(p, unicode: true)]];
  static final RegExp _echoEnd = RegExp(r'[.:!?。：！？]+\s*|\n\s*|$');

  static String _stripTranslatedEcho(String out) {
    final s = out.trimLeft();
    final low = s.toLowerCase();
    for (final m in _echoEnd.allMatches(s)) {
      if (m.start > 240) break;
      if (m.start > 0) {
        final head = low.substring(0, m.start);
        if (_echoWordsRx.any((row) => row.every((rx) => rx.hasMatch(head)))) {
          return s.substring(m.end);
        }
      }
      // A real sentence end or line break: the echo is one sentence ("Note:" lead-ins go on).
      if (m.group(0)!.replaceAll(RegExp(r'[ \t:：]'), '').isNotEmpty) break;
      if (m.end >= s.length) break;
    }
    return out;
  }

  /// Desktop `hymt.strip_echo`: drops the card instruction when the model repeats it before (or
  /// instead of) the translation, in English, Chinese or the target language (#1615). An echo-only
  /// output becomes empty, so the line is shown untranslated.
  static String stripEcho(String out) {
    out = out.replaceFirst(_echoEn, '').replaceFirst(_echoZh, '');
    return _stripTranslatedEcho(out).trim();
  }

  /// True once the streamed output holds a finished line that is not just the echoed instruction
  /// (#1615): the translator stops there. An echo line alone does not stop it, so a translation the
  /// model writes after the echo is still read.
  static bool lineDone(String streamed) {
    final s = streamed.trimLeft();
    final nl = s.lastIndexOf('\n');
    return nl > 0 && clean(s.substring(0, nl)).isNotEmpty;
  }

  /// Strips template tokens (`<｜...｜>`), the echoed card instruction, labels and wrapping quotes
  /// from raw model output; keeps only the first non-empty line.
  static String clean(String raw) {
    var t = stripEcho(raw.replaceAll(RegExp(r'<｜[^｜]*｜>'), '').trim());
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
