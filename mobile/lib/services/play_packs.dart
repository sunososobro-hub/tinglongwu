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

  /// Shows Play's "download on mobile data?" dialog again (while waiting for Wi-Fi).
  static Future<void> confirmMobileData() async {
    try {
      await _ch.invokeMethod('playConfirm');
    } catch (_) {}
  }

  /// Waits for (or restarts) the Play download of the packs. Nothing is copied: once the packs are
  /// on the device the app reads the models from them. [onState] gets (bytes, total, state), state
  /// as in PlayPacks.kt `stateName`.
  static Future<PackFetch> fetch(
      {required void Function(int done, int total, String state) onState}) async {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'playProgress') {
        final m = call.arguments as Map;
        onState((m['done'] as num).toInt(), (m['total'] as num).toInt(), '${m['status']}');
      }
    });
    try {
      final paths = await _ch.invokeMapMethod<String, String>('playFetch') ?? const {};
      return PackFetch(packs: paths.keys.toList());
    } on PlatformException catch (e) {
      return e.code == 'CANCELLED'
          ? const PackFetch(error: 'cancelled')
          : PackFetch(error: e.details is String ? e.details as String : 'error', message: e.message);
    } finally {
      _ch.setMethodCallHandler(null);
    }
  }

  /// [fetch] for the Import screen (progress bar + report lines).
  static Future<ImportReport> download(
      {required void Function(int done, int total, String label) onProgress}) async {
    final f = await fetch(onState: (d, t, _) => onProgress(d, t, 'Google Play'));
    final r = ImportReport()..imported.addAll(f.packs);
    if (f.error == 'cancelled') {
      r.cancelled = true;
    } else if (f.error != null) {
      r.problems.add('Google Play: ${f.message ?? f.error}');
    }
    return r;
  }
}

class PackFetch {
  const PackFetch({this.packs = const [], this.error, this.message});
  final List<String> packs;

  /// null = done; else network, storage, cancelled or a Play error code.
  final String? error;
  final String? message;
}
