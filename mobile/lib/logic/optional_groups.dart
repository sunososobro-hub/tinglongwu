import 'lang_pack.dart';
import 'model_manifest.dart';

/// Optional language group of `models_manifest.json` (`optional_groups`, #1833), e.g. `lang-ru`:
/// its own zip, imported through the same flow as the models.
class OptionalGroup {
  const OptionalGroup({
    required this.id,
    required this.zip,
    required this.files,
    this.note = '',
  });
  final String id;
  final String zip;
  final String note;
  final List<ManifestEntry> files;

  /// `lang-ru` -> `ru`.
  String get lang => id.startsWith('lang-') ? id.substring(5) : id;

  int get size => files.fold(0, (a, f) => a + f.size);

  factory OptionalGroup.fromJson(String id, Map<String, dynamic> j) => OptionalGroup(
        id: id,
        zip: j['zip'] as String,
        note: (j['note'] as String?) ?? '',
        files: [
          for (final f in j['files'] as List)
            ManifestEntry.fromJson({...(f as Map<String, dynamic>), 'group': id})
        ],
      );
}

/// A pack the device has, as the Import screen lists it.
class InstalledPack {
  const InstalledPack(this.id, this.name);
  final String id;
  final String name;
}

const defaultPackId = 'default';

/// Packs present for [present] (target paths on disk): the default pack when every required
/// model file is there, plus each optional language group whose files are all there.
List<InstalledPack> detectPacks(ModelManifest m, Set<String> present) => [
      if (m.missingRequired(present).isEmpty) const InstalledPack(defaultPackId, '預設 / default'),
      for (final g in m.optionalGroups)
        if (g.files.every((f) => present.contains(f.path)))
          InstalledPack(g.id, '${g.lang} (${g.zip})'),
    ];

/// GigaAM v3 (sherpa-onnx export) layout of the `lang-ru` group, relative to the models dir.
class GigaamLayout {
  static const dir = 'gigaam3';
  static const files = ['encoder.int8.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt'];
  static List<String> get rel => [for (final f in files) '$dir/$f'];
}

/// Which recognizer hears the other person's [lang] in 對話翻譯 (pure, #1834): GigaAM for Russian
/// only when the `lang-ru` group is installed, else the earlier extra pack (`asr-<lang>`), else null.
AsrPackPaths? pickExtraAsr(
  String lang, {
  required String Function(String rel) resolve,
  required bool Function(String path) exists,
}) {
  if (lang == 'ru') {
    final p = [for (final r in GigaamLayout.rel) resolve(r)];
    if (p.every(exists)) {
      return AsrPackPaths(lang: lang, encoder: p[0], decoder: p[1], joiner: p[2], tokens: p[3]);
    }
  }
  return AsrPackPaths.find(lang, resolve: resolve, exists: exists);
}
