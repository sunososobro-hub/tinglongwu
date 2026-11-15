import 'package:flutter/services.dart';

import 'model_importer.dart';

/// Models from Google Play Asset Delivery (PlayPacks.kt): two fast-follow packs that Play
/// downloads right after install, so the app keeps no INTERNET permission. The files are read in
/// place from the pack folders (ModelPaths), never copied. Only when installed from Play.
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

  /// Waits for (or restarts) the Play download of the packs. Nothing is copied: once the packs are
  /// on the device the app reads the models from them. [onProgress] gets (bytes, total, label).
  static Future<ImportReport> download(
      {required void Function(int done, int total, String label) onProgress}) async {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'playProgress') {
        final m = call.arguments as Map;
        onProgress((m['done'] as num).toInt(), (m['total'] as num).toInt(), 'Google Play');
      }
    });
    final r = ImportReport();
    try {
      final paths = await _ch.invokeMapMethod<String, String>('playFetch') ?? const {};
      r.imported.addAll(paths.keys);
    } on PlatformException catch (e) {
      if (e.code == 'CANCELLED') {
        r.cancelled = true;
      } else {
        r.problems.add('Google Play: ${e.message}');
      }
    } finally {
      _ch.setMethodCallHandler(null);
    }
    return r;
  }
}
