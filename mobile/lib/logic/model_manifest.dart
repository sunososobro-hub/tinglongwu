import 'dart:convert';

import 'lang_pack.dart';
import 'speech_out.dart';

/// One model file the app knows: where it goes under `models/`, and its exact size and SHA-256.
class ManifestEntry {
  const ManifestEntry({
    required this.path,
    required this.size,
    required this.sha256,
    required this.group,
    this.url,
    this.alias,
  });

  /// Relative path under the models dir, always with `/` (e.g. `sensevoice/tokens.txt`).
  final String path;
  final int size;
  final String sha256;

  /// `asr`, `vad` or `llm`. All asr + vad files are required; at least one llm file is required.
  /// Voice pack files are `tts`, extra-language recognizer files are `lang` and the speaker model
  /// is `speaker` (all optional).
  final String group;

  /// Official download link for files that are not in the bundle zip (e.g. the 4B model).
  final String? url;

  /// Upstream file name accepted besides [basename] (e.g. `vits-ljs.onnx` for `tts-en/model.onnx`).
  final String? alias;

  String get basename => path.split('/').last;

  /// Names this file is picked by: [basename] and [alias].
  List<String> get names => [basename, ?alias];

  factory ManifestEntry.fromJson(Map<String, dynamic> j) => ManifestEntry(
        path: j['path'] as String,
        size: (j['size'] as num).toInt(),
        sha256: (j['sha256'] as String).toLowerCase(),
        group: j['group'] as String,
        url: j['url'] as String?,
        alias: j['alias'] as String?,
      );
}

/// An optional pack: a read-aloud voice (#558) or an extra-language recognizer (#556), its own
/// zip, imported through the same flow as the models. Not a required model and not in the models zip or the Play packs (those tools read
/// `models_manifest.json` only); the voices live in `assets/voice_packs.json`.
class VoicePack {
  VoicePack({
    required this.id,
    required this.lang,
    required this.name,
    required this.url,
    required this.files,
    this.note = '',
    this.source = '',
    this.kind = vitsKind,
  });

  /// [kind] of a read-aloud voice (sherpa-onnx VITS + lexicon, or Supertonic) and of an
  /// extra-language recognizer (sherpa-onnx transducer).
  static const vitsKind = TtsVoiceLayout.vits;
  static const supertonicKind = TtsVoiceLayout.supertonic;
  static const asrKind = 'asr';
  static const kinds = {vitsKind, supertonicKind, asrKind};

  final String id;
  final String kind;

  /// Language the pack is for; its files live under [folder].
  final String lang;

  bool get isAsr => kind == asrKind;

  /// `tts-<lang>` (see [TtsVoiceLayout]) or `asr-<lang>` (see [AsrPackLayout]).
  String get folder => isAsr ? AsrPackLayout.dir(lang) : TtsVoiceLayout.dir(lang);

  /// The exact files the loader reads, relative to the models dir.
  List<String> get layout =>
      isAsr ? AsrPackLayout.rel(lang) : TtsVoiceLayout.rel(lang, engine: kind);

  /// Zip file name (as downloaded); a picked zip with this name is read as this pack.
  final String name;
  final String url;
  final String note;

  /// Official upstream archive the files come from (unmodified).
  final String source;
  final List<ManifestEntry> files;

  int get size => files.fold(0, (a, f) => a + f.size);

  /// Zip names read as this pack: [name], and the upstream archive name with `.zip`
  /// (`vits-ljs.tar.bz2` -> `vits-ljs.zip`, for a re-zipped upstream folder).
  List<String> get zipNames {
    final stem = source.split('/').last.split('.').first;
    return [name, if (stem.isNotEmpty) '$stem.zip'];
  }

  factory VoicePack.fromJson(Map<String, dynamic> j) => VoicePack(
        id: j['id'] as String,
        lang: j['lang'] as String,
        kind: (j['kind'] as String?) ?? vitsKind,
        name: j['name'] as String,
        url: j['url'] as String,
        note: (j['note'] as String?) ?? '',
        source: (j['source'] as String?) ?? '',
        files: [
          for (final f in j['files'] as List)
            ManifestEntry.fromJson({
              ...(f as Map<String, dynamic>),
              'group': (j['kind'] ?? vitsKind) == asrKind ? 'lang' : 'tts'
            })
        ],
      );
}

enum Verdict { ok, unknownFile, sizeMismatch, hashMismatch }

class ValidationResult {
  const ValidationResult(this.verdict, {this.entry, this.detail = ''});
  final Verdict verdict;
  final ManifestEntry? entry;
  final String detail;
  bool get ok => verdict == Verdict.ok;
}

/// The models manifest (assets/models_manifest.json) plus the optional voice packs
/// (assets/voice_packs.json): the only files the importer accepts.
class ModelManifest {
  ModelManifest({
    required this.files,
    required this.bundleName,
    required this.bundleUrl,
    this.bundleNote = '',
    this.packs = const [],
    this.speakerModel,
    this.speakerNote = '',
  }) {
    // File names must be unique among the models and inside each voice pack (a zip is matched by
    // file name); a voice may reuse a model's name (`tokens.txt`) because its zip is read alone.
    void unique(Iterable<ManifestEntry> group) {
      final seen = <String>{};
      for (final f in group) {
        for (final n in f.names) {
          if (!seen.add(n)) throw FormatException('duplicate file name in manifest: $n');
        }
      }
    }

    unique(files);
    final ids = <String>{};
    for (final p in packs) {
      if (!ids.add(p.id)) throw FormatException('duplicate pack id: ${p.id}');
      unique(p.files);
      if (!VoicePack.kinds.contains(p.kind)) throw FormatException('unknown pack kind: ${p.kind}');
      // The pack must be exactly what its loader reads (TtsVoicePaths / AsrPackPaths), nothing else.
      final want = p.layout.toSet();
      final got = p.files.map((f) => f.path).toSet();
      if (got.length != p.files.length || got.length != want.length || !got.containsAll(want)) {
        throw FormatException('pack ${p.id} must hold exactly ${want.join(', ')}');
      }
    }
    final paths = <String>{};
    for (final f in allFiles) {
      if (!paths.add(f.path)) throw FormatException('duplicate path in manifest: ${f.path}');
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(f.sha256)) {
        throw FormatException('bad sha256 for ${f.path}');
      }
      if (f.size <= 0) throw FormatException('bad size for ${f.path}');
      if (f.path.startsWith('/') || f.path.split('/').contains('..')) {
        throw FormatException('unsafe path: ${f.path}');
      }
    }
  }

  /// The models (required asr/vad, one llm). Voice files are in [packs].
  final List<ManifestEntry> files;
  final String bundleName;
  final String bundleUrl;
  final String bundleNote;

  /// Optional read-aloud voices (#558).
  final List<VoicePack> packs;

  /// Optional speaker-embedding model for the 說話者 N labels (#1155): one plain file, imported
  /// like a voice, never required and not in the models zip or the Play packs.
  final ManifestEntry? speakerModel;
  final String speakerNote;

  /// Models, voice files and the speaker model.
  List<ManifestEntry> get allFiles =>
      [...files, for (final p in packs) ...p.files, ?speakerModel];

  /// [json] is `models_manifest.json`; [packsJson] (optional) is `voice_packs.json`.
  factory ModelManifest.parse(String json, {String? packsJson}) {
    final j = jsonDecode(json) as Map<String, dynamic>;
    final b = j['bundle'] as Map<String, dynamic>;
    final pj = packsJson == null ? null : jsonDecode(packsJson) as Map<String, dynamic>;
    return ModelManifest(
      files: [
        for (final f in j['files'] as List) ManifestEntry.fromJson(f as Map<String, dynamic>)
      ],
      bundleName: b['name'] as String,
      bundleUrl: b['url'] as String,
      bundleNote: (b['note'] as String?) ?? '',
      packs: [
        for (final p in (pj?['packs'] as List?) ?? const [])
          VoicePack.fromJson(p as Map<String, dynamic>)
      ],
      speakerModel: pj?['speaker'] == null
          ? null
          : ManifestEntry.fromJson({...(pj!['speaker'] as Map<String, dynamic>), 'group': 'speaker'}),
      speakerNote: ((pj?['speaker'] as Map?)?['note'] as String?) ?? '',
    );
  }

  /// Basename -> target path, for picking matching entries out of the models zip.
  Map<String, String> get basenameTargets => {for (final f in files) f.basename: f.path};

  /// The voice pack a picked zip is, by its (download-renamed) name; null for any other zip.
  VoicePack? packForZip(String zipName) {
    final n = normalizeName(zipName).toLowerCase();
    for (final p in packs) {
      if (p.zipNames.any((z) => normalizeName(z).toLowerCase() == n)) return p;
    }
    return null;
  }

  /// Name -> target path for the entries to take out of the zip [zipName]: a voice pack's files
  /// (by file name or upstream alias) when the zip is that pack, else the models.
  Map<String, String> zipTargetsFor(String zipName) {
    final p = packForZip(zipName);
    if (p == null) return basenameTargets;
    return {
      for (final f in p.files)
        for (final n in f.names) n: f.path
    };
  }

  /// Undo the renames browsers and download managers apply to repeated downloads:
  /// `Qwen3-4B-Q4_K_M (1).gguf`, `silero_vad-1.onnx`, `tokens(2).txt`.
  static String normalizeName(String name) {
    var n = name.replaceAll('\\', '/').split('/').last.trim();
    final dot = n.lastIndexOf('.');
    if (dot <= 0) return n;
    var stem = n.substring(0, dot);
    final ext = n.substring(dot);
    stem = stem.replaceFirst(RegExp(r'\s*\(\d+\)$'), '');
    stem = stem.replaceFirst(RegExp(r'-\d+$'), '');
    return '$stem$ext';
  }

  /// Which model file a picked file or zip entry is, or null if it is not one we accept.
  /// Exact basename wins; otherwise the download-renamed form is tried.
  ManifestEntry? resolve(String sourceName) {
    final raw = sourceName.replaceAll('\\', '/').split('/').last.trim();
    for (final f in files) {
      if (f.basename == raw) return f;
    }
    final n = normalizeName(raw);
    for (final f in files) {
      if (f.basename == n) return f;
    }
    return null;
  }

  /// Every file a single picked file may be, models first (exact name before the
  /// download-renamed form): `tokens.txt` may be the ASR's or a voice's. The importer keeps it
  /// where size and SHA-256 match ([validateAny]).
  List<ManifestEntry> candidates(String sourceName) {
    final raw = sourceName.replaceAll('\\', '/').split('/').last.trim();
    final out = <ManifestEntry>[];
    for (final name in [raw, normalizeName(raw)]) {
      for (final f in allFiles) {
        if (f.names.contains(name) && !out.contains(f)) out.add(f);
      }
    }
    return out;
  }

  ManifestEntry? byPath(String path) {
    for (final f in allFiles) {
      if (f.path == path) return f;
    }
    return null;
  }

  /// Checks a copied file (target path, byte count, SHA-256 hex) against the manifest.
  ValidationResult validate(String targetPath, int size, String sha256) {
    final e = byPath(targetPath);
    if (e == null) return ValidationResult(Verdict.unknownFile, detail: targetPath);
    if (size != e.size) {
      return ValidationResult(Verdict.sizeMismatch,
          entry: e, detail: 'expected ${e.size} bytes, got $size');
    }
    if (sha256.toLowerCase() != e.sha256) {
      return ValidationResult(Verdict.hashMismatch,
          entry: e, detail: 'SHA-256 differs (file corrupt or a different version)');
    }
    return ValidationResult(Verdict.ok, entry: e);
  }

  /// [validate] against each of [targetPaths] in turn: the first match, else the first result.
  ValidationResult validateAny(List<String> targetPaths, int size, String sha256) {
    ValidationResult? first;
    for (final t in targetPaths) {
      final v = validate(t, size, sha256);
      if (v.ok) return v;
      first ??= v;
    }
    return first ?? const ValidationResult(Verdict.unknownFile);
  }

  /// Required files still missing, given the set of target paths present on disk.
  /// Any one llm file satisfies the llm requirement (reported as the bundle's default when none).
  /// Voice packs are optional and never reported here.
  List<String> missingRequired(Set<String> present) {
    final out = <String>[
      for (final f in files)
        if (f.group != 'llm' && !present.contains(f.path)) f.path,
    ];
    final llms = files.where((f) => f.group == 'llm').toList();
    if (llms.isNotEmpty && !llms.any((f) => present.contains(f.path))) {
      final inBundle = llms.where((f) => f.url == null);
      out.add((inBundle.isEmpty ? llms.first : inBundle.first).path);
    }
    return out;
  }

  /// True when the optional speaker model is in [present] (target paths).
  bool speakerModelInstalled(Set<String> present) =>
      speakerModel != null && present.contains(speakerModel!.path);

  /// Ids of the voice packs whose files are all in [present] (target paths).
  Set<String> installedPacks(Set<String> present) => {
        for (final p in packs)
          if (p.files.every((f) => present.contains(f.path))) p.id
      };
}
