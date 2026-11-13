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

  static final RegExp _label = RegExp(
    r'^(ZH|翻譯|翻译|譯文|译文|Translation)\s*[:：]\s*',
    caseSensitive: false,
  );

  /// Strips template tokens (`<｜...｜>`), labels and wrapping quotes from raw
  /// model output; keeps only the first non-empty line.
  static String clean(String raw) {
    var t = raw.replaceAll(RegExp(r'<｜[^｜]*｜>'), '').trim();
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
