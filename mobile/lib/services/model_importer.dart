import 'dart:io';

import 'package:flutter/services.dart';

import '../logic/model_manifest.dart';

class PickedDoc {
  PickedDoc(this.uri, this.name, this.size);
  final String uri;
  final String name;
  final int size; // -1 if unknown
  bool get isZip => name.toLowerCase().endsWith('.zip');
}

class ImportReport {
  final List<String> imported = [];
  final List<String> problems = []; // human-readable, one per file
  bool cancelled = false;
}

/// Glue between the Import screen, the Android picker/copier (ModelImporter.kt) and the manifest.
class ModelImporter {
  ModelImporter(this.manifest, this.modelsDir);

  final ModelManifest manifest;
  final String modelsDir;

  static const _ch = MethodChannel('tlwu/native');
  static const manifestAsset = 'assets/models_manifest.json';

  static Future<ModelManifest> loadManifest() async =>
      ModelManifest.parse(await rootBundle.loadString(manifestAsset));

  String get _staging => '$modelsDir/.import';

  /// Target paths of manifest files already in the models dir (by name and size; the hash was
  /// checked when they were imported).
  Set<String> presentFiles() => {
        for (final f in manifest.files)
          if (_sizeOf('$modelsDir/${f.path}') == f.size) f.path,
      };

  static int _sizeOf(String p) {
    try {
      final f = File(p);
      return f.existsSync() ? f.lengthSync() : -1;
    } catch (_) {
      return -1;
    }
  }

  static Future<List<PickedDoc>> pick() async {
    final r = await _ch.invokeListMethod<Map<dynamic, dynamic>>('pickDocuments') ?? const [];
    return [
      for (final m in r)
        PickedDoc(m['uri'] as String, m['name'] as String, (m['size'] as num?)?.toInt() ?? -1)
    ];
  }

  static Future<bool> openUrl(String url) async =>
      await _ch.invokeMethod<bool>('openUrl', {'url': url}) ?? false;

  static Future<void> cancel() => _ch.invokeMethod('cancelImport');

  Future<int> freeBytes() async =>
      (await _ch.invokeMethod<num>('freeBytes', {'dir': modelsDir}))?.toInt() ?? -1;

  /// Imports every picked doc in turn. [onProgress] gets (bytes read so far over all docs, total).
  Future<ImportReport> run(List<PickedDoc> docs,
      {required void Function(int done, int total, String name) onProgress}) async {
    final report = ImportReport();
    final staging = Directory(_staging);
    // Leftovers of an import the system killed would count against the free-space check.
    try {
      if (staging.existsSync()) staging.deleteSync(recursive: true);
    } catch (_) {}
    final total = docs.fold<int>(0, (a, d) => a + (d.size > 0 ? d.size : 0));
    final free = await freeBytes();
    if (free >= 0 && total > 0 && free < total + (64 << 20)) {
      report.problems.add('Not enough free space: need ${_mb(total)}, have ${_mb(free)}.');
      return report;
    }
    var base = 0;
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'importProgress') {
        final m = call.arguments as Map;
        onProgress(base + (m['done'] as num).toInt(), total, _current);
      }
    });
    try {
      for (final d in docs) {
        _current = d.name;
        onProgress(base, total, d.name);
        final args = <String, Object?>{'uri': d.uri, 'stagingDir': _staging, 'size': d.size};
        if (d.isZip) {
          args['zipTargets'] = manifest.basenameTargets;
        } else {
          final e = manifest.resolve(d.name);
          if (e == null) {
            report.problems.add('${d.name}: not a model file this app uses (skipped).');
            base += d.size > 0 ? d.size : 0;
            continue;
          }
          args['target'] = e.path;
        }
        if (staging.existsSync()) staging.deleteSync(recursive: true);
        staging.createSync(recursive: true);
        List<Map<dynamic, dynamic>> copied;
        try {
          copied = await _ch.invokeListMethod<Map<dynamic, dynamic>>('importDocument', args) ?? [];
        } on PlatformException catch (e) {
          if (e.code == 'CANCELLED') {
            report.cancelled = true;
            break;
          }
          report.problems.add('${d.name}: ${e.message}');
          continue;
        }
        if (d.isZip && copied.isEmpty) {
          report.problems.add('${d.name}: no model files found in this zip.');
        }
        for (final c in copied) {
          final target = c['target'] as String;
          final v = manifest.validate(target, (c['size'] as num).toInt(), c['sha256'] as String);
          final src = File('$_staging/$target');
          if (!v.ok) {
            report.problems.add('$target: ${v.detail}');
            if (src.existsSync()) src.deleteSync();
            continue;
          }
          final dst = File('$modelsDir/$target');
          dst.parent.createSync(recursive: true);
          if (dst.existsSync()) dst.deleteSync();
          src.renameSync(dst.path);
          report.imported.add(target);
        }
        base += d.size > 0 ? d.size : 0;
      }
    } finally {
      _ch.setMethodCallHandler(null);
      try {
        if (staging.existsSync()) staging.deleteSync(recursive: true);
      } catch (_) {}
    }
    return report;
  }

  String _current = '';

  static String _mb(int b) => '${(b / (1 << 20)).toStringAsFixed(0)} MB';
}
