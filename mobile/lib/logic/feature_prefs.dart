import 'dart:io';

/// Optional-feature switches, kept in a small "key=value" text file (unknown keys are ignored,
/// missing or bad values fall back to the defaults).
class FeaturePrefs {
  const FeaturePrefs({this.sourceDraft = true});

  /// 顯示原文草稿 (#1064): grey source text while a sentence is still being spoken. Default on.
  final bool sourceDraft;

  FeaturePrefs copyWith({bool? sourceDraft}) =>
      FeaturePrefs(sourceDraft: sourceDraft ?? this.sourceDraft);

  String format() => 'sourceDraft=${sourceDraft ? 1 : 0}\n';

  static FeaturePrefs parse(String? s) {
    final m = <String, String>{};
    for (final line in (s ?? '').split('\n')) {
      final i = line.indexOf('=');
      if (i > 0) m[line.substring(0, i).trim()] = line.substring(i + 1).trim();
    }
    bool b(String k, bool d) => switch (m[k]) {
          '1' || 'true' => true,
          '0' || 'false' => false,
          _ => d,
        };
    return FeaturePrefs(sourceDraft: b('sourceDraft', true));
  }

  @override
  bool operator ==(Object other) => other is FeaturePrefs && other.sourceDraft == sourceDraft;

  @override
  int get hashCode => sourceDraft.hashCode;
}

class FeaturePrefsStore {
  FeaturePrefsStore(this.path);
  final String path;

  FeaturePrefs load() {
    try {
      return FeaturePrefs.parse(File(path).readAsStringSync());
    } catch (_) {
      return const FeaturePrefs();
    }
  }

  void save(FeaturePrefs p) {
    try {
      File(path).writeAsStringSync(p.format());
    } catch (_) {}
  }
}
