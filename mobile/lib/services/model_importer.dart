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
  static const voicePacksAsset = 'assets/voice_packs.json';

  static Future<ModelManifest> loadManifest() async => ModelManifest.parse(
      await rootBundle.loadString(manifestAsset),
      packsJson: await rootBundle.loadString(voicePacksAsset));

  String get _staging => '$modelsDir/.import';

  /// Target paths of manifest files already in the models dir (by name and size; the hash was
  /// checked when they were imported) or in one of the Play pack folders [packDirs].
  Set<String> presentFiles({List<String> packDirs = const []}) => {
        for (final f in manifest.allFiles)
          if ([modelsDir, ...packDirs].any((d) => _sizeOf('$d/${f.path}') == f.size)) f.path,
      };

  static int _sizeOf(String p) {
    try {
      final f = File(p);
      return f.existsSync() ? f.lengthSync() : -1;
    } catch (_) {
      return -1;
    }
  }

  /// [downloads]: open the picker in the Downloads folder (a pack zip the browser just saved).
  static Future<List<PickedDoc>> pick({bool downloads = false}) async {
    final r = await _ch.invokeListMethod<Map<dynamic, dynamic>>(
            'pickDocuments', {'downloads': downloads}) ??
        const [];
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
        // A single file may be one of several manifest files (`tokens.txt`: ASR or a voice); it is
        // copied to the first and kept where size and SHA-256 match.
        var options = const <String>[];
        if (d.isZip) {
          args['zipTargets'] = manifest.zipTargetsFor(d.name);
        } else {
          options = [for (final e in manifest.candidates(d.name)) e.path];
          if (options.isEmpty) {
            report.problems.add('${d.name}: not a model file this app uses (skipped).');
            base += d.size > 0 ? d.size : 0;
            continue;
          }
          args['target'] = options.first;
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
          final staged = c['target'] as String;
          final v = manifest.validateAny(d.isZip ? [staged] : options,
              (c['size'] as num).toInt(), c['sha256'] as String);
          final src = File('$_staging/$staged');
          if (!v.ok) {
            report.problems.add('$staged: ${v.detail}');
            if (src.existsSync()) src.deleteSync();
            continue;
          }
          final target = v.entry!.path;
          final dst = File('$modelsDir/$target');
          dst.parent.createSync(recursive: true);
          if (dst.existsSync()) dst.deleteSync();
          src.renameSync(dst.path);
          report.imported.add(target);
        }
        base += d.size > 0 ? d.size : 0;
      }
    } catch (e) {
      // keep what was already imported in the report; just add the failure
      report.problems.add('$_current: $e');
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
