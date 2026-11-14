import 'dart:io';

import 'package:flutter/services.dart';

import 'model_importer.dart';

/// Models from Google Play Asset Delivery (PlayPacks.kt). Only when the app was installed from
/// Play; the Play Store downloads the packs, so the app keeps no INTERNET permission.
class PlayPacks {
  static const _ch = MethodChannel('tlwu/native');

  /// True when Play can deliver the model packs to this install.
  static Future<bool> available() async {
    try {
      final m = await _ch.invokeMapMethod<String, dynamic>('playStatus');
      return m?['available'] == true;
    } catch (_) {
      return false; // e.g. an old build without the channel method
    }
  }

  static Future<void> cancel() => _ch.invokeMethod('playCancel');

  /// Downloads the packs, then copies every model file through [imp] (same size + SHA-256 check
  /// as a manual import) and removes the packs to free the space.
  /// [onProgress] gets (bytes, total, label) for the download and then for the copy.
  static Future<ImportReport> downloadAndImport(ModelImporter imp,
      {required void Function(int done, int total, String label) onProgress}) async {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'playProgress') {
        final m = call.arguments as Map;
        onProgress((m['done'] as num).toInt(), (m['total'] as num).toInt(), 'Google Play');
      }
    });
    Map<String, String> paths;
    try {
      paths = (await _ch.invokeMapMethod<String, String>('playFetch')) ?? const {};
    } on PlatformException catch (e) {
      final r = ImportReport();
      if (e.code == 'CANCELLED') {
        r.cancelled = true;
      } else {
        r.problems.add('Google Play: ${e.message}');
      }
      return r;
    } finally {
      _ch.setMethodCallHandler(null);
    }
    final docs = docsFromPacks(imp, paths.values);
    if (docs.isEmpty) {
      return ImportReport()..problems.add('Google Play: no model files in the downloaded packs.');
    }
    final report = await imp.run(docs, onProgress: onProgress);
    if (!report.cancelled && report.problems.isEmpty) {
      try {
        await _ch.invokeMethod('playRemove');
      } catch (_) {} // the copy is done; a pack left behind only costs space
    }
    return report;
  }

  /// Manifest files found in the pack folders that are not already in the models dir.
  static List<PickedDoc> docsFromPacks(ModelImporter imp, Iterable<String> packDirs) {
    final present = imp.presentFiles();
    final docs = <PickedDoc>[];
    for (final f in imp.manifest.files) {
      if (present.contains(f.path)) continue;
      for (final dir in packDirs) {
        final file = File('$dir/${f.path}');
        if (file.existsSync()) {
          docs.add(PickedDoc(Uri.file(file.path).toString(), f.basename, file.lengthSync()));
          break;
        }
      }
    }
    return docs;
  }
}
