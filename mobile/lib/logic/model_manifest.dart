import 'dart:convert';

/// One model file the app knows: where it goes under `models/`, and its exact size and SHA-256.
class ManifestEntry {
  const ManifestEntry({
    required this.path,
    required this.size,
    required this.sha256,
    required this.group,
    this.url,
  });

  /// Relative path under the models dir, always with `/` (e.g. `sensevoice/tokens.txt`).
  final String path;
  final int size;
  final String sha256;

  /// `asr`, `vad` or `llm`. All asr + vad files are required; at least one llm file is required.
  final String group;

  /// Official download link for files that are not in the bundle zip (e.g. the 4B model).
  final String? url;

  String get basename => path.split('/').last;

  factory ManifestEntry.fromJson(Map<String, dynamic> j) => ManifestEntry(
        path: j['path'] as String,
        size: (j['size'] as num).toInt(),
        sha256: (j['sha256'] as String).toLowerCase(),
        group: j['group'] as String,
        url: j['url'] as String?,
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

/// The models manifest (assets/models_manifest.json): the only files the importer accepts.
class ModelManifest {
  ModelManifest({
    required this.files,
    required this.bundleName,
    required this.bundleUrl,
    this.bundleNote = '',
  }) {
    final seen = <String>{};
    for (final f in files) {
      if (!seen.add(f.basename)) {
        throw FormatException('duplicate file name in manifest: ${f.basename}');
      }
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(f.sha256)) {
        throw FormatException('bad sha256 for ${f.path}');
      }
      if (f.size <= 0) throw FormatException('bad size for ${f.path}');
      if (f.path.startsWith('/') || f.path.split('/').contains('..')) {
        throw FormatException('unsafe path: ${f.path}');
      }
    }
  }

  final List<ManifestEntry> files;
  final String bundleName;
  final String bundleUrl;
  final String bundleNote;

  factory ModelManifest.parse(String json) {
    final j = jsonDecode(json) as Map<String, dynamic>;
    final b = j['bundle'] as Map<String, dynamic>;
    return ModelManifest(
      files: [
        for (final f in j['files'] as List) ManifestEntry.fromJson(f as Map<String, dynamic>)
      ],
      bundleName: b['name'] as String,
      bundleUrl: b['url'] as String,
      bundleNote: (b['note'] as String?) ?? '',
    );
  }

  /// Basename -> target path, for picking matching entries out of a zip.
  Map<String, String> get basenameTargets => {for (final f in files) f.basename: f.path};

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

  /// Which manifest file a picked file or zip entry is, or null if it is not one we accept.
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

  ManifestEntry? byPath(String path) {
    for (final f in files) {
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

  /// Required files still missing, given the set of target paths present on disk.
  /// Any one llm file satisfies the llm requirement (reported as the bundle's default when none).
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
}
