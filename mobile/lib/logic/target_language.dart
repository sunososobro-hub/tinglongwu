import 'dart:io';

/// Subtitle (target) language picked by the user, remembered between runs (#730).
/// Pure logic + a one-line text file, so it is testable without Flutter plugins.
class TargetLanguage {
  /// Code -> menu label. zh = zh-Hant (Taiwan), the default.
  static const options = <String, String>{
    'zh': '繁體中文',
    'en': 'English',
  };
  static const fallback = 'zh';

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
