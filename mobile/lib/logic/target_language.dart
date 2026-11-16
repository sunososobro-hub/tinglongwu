import 'dart:io';

/// Subtitle (target) language picked by the user, remembered between runs (#730).
/// Pure logic + a one-line text file, so it is testable without Flutter plugins.
class TargetLanguage {
  /// Code -> menu label. zh = zh-Hant (Taiwan), the default.
  static const options = <String, String>{
    'zh': '繁體中文',
    'en': 'English',
    'ja': '日本語',
    'ko': '한국어',
    'es': 'Español',
    'fr': 'Français',
    'de': 'Deutsch',
    'th': 'ไทย',
    'vi': 'Tiếng Việt',
    'id': 'Bahasa Indonesia',
    'ru': 'Русский',
  };

  /// Languages the ASR (SenseVoice) can detect, so the only ones usable as "my language" in
  /// two-way conversation mode.
  static const spoken = <String>['zh', 'en', 'ja', 'ko'];
  static const fallback = 'zh';

  /// Selector text, read as a target: "翻成：繁體中文".
  static String targetLabel(String code) => '翻成：${options[normalize(code)]}';

  /// The source language is auto-detected by the ASR (SenseVoice), so there is no source picker.
  /// Short source label for the header pill.
  static const sourceShort = '自動辨識';

  static const sourceHint = '自動辨識說話語言';

  /// Unknown / empty / null values fall back to zh.
  static String normalize(String? code) {
    final c = (code ?? '').trim().toLowerCase();
    return options.containsKey(c) ? c : fallback;
  }
}

class TargetLanguageStore {
  TargetLanguageStore(this.path);
  final String path;

  String load() {
    try {
      return TargetLanguage.normalize(File(path).readAsStringSync());
    } catch (_) {
      return TargetLanguage.fallback;
    }
  }

  void save(String code) {
    try {
      File(path).writeAsStringSync(TargetLanguage.normalize(code));
    } catch (_) {}
  }
}
