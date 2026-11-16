import 'dart:io';

/// Optional-feature switches, kept in a small "key=value" text file (unknown keys are ignored,
/// missing or bad values fall back to the defaults).
class FeaturePrefs {
  const FeaturePrefs({this.sourceDraft = true, this.zhDraft = false, this.tiltFlip = true});

  /// 顯示原文草稿 (#1064): grey source text while a sentence is still being spoken. Default on.
  final bool sourceDraft;

  /// 中文草稿 (#1065): translate the draft at most every 2 s. Default off (heavy); only the
  /// user turns it on, the probe merely recommends.
  final bool zhDraft;

  /// 傾斜翻面 (#1146): auto-flip the subtitle screen when the phone is tipped over to show the
  /// other person. Default on; the header lock icon turns it off.
  final bool tiltFlip;

  FeaturePrefs copyWith({bool? sourceDraft, bool? zhDraft, bool? tiltFlip}) => FeaturePrefs(
      sourceDraft: sourceDraft ?? this.sourceDraft,
      zhDraft: zhDraft ?? this.zhDraft,
      tiltFlip: tiltFlip ?? this.tiltFlip);

  String format() =>
      'sourceDraft=${sourceDraft ? 1 : 0}\nzhDraft=${zhDraft ? 1 : 0}\ntiltFlip=${tiltFlip ? 1 : 0}\n';

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
    return FeaturePrefs(sourceDraft: b('sourceDraft', true), zhDraft: b('zhDraft', false),
        tiltFlip: b('tiltFlip', true));
  }

  @override
  bool operator ==(Object other) => other is FeaturePrefs && other.sourceDraft == sourceDraft && other.zhDraft == zhDraft &&
      other.tiltFlip == tiltFlip;

  @override
  int get hashCode => Object.hash(sourceDraft, zhDraft, tiltFlip);
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
