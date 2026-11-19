import 'package:flutter/services.dart';

import '../logic/pack_url.dart';

/// Dart side of the one-tap pack download (#1567). The network work is in PackDownloader.kt;
/// this file is the only Dart file allowed to talk to it (scripts/check_mobile_network.py).
class PackDownloader {
  static const _ch = MethodChannel('tlwu/native');
  static bool _active = false;

  /// Downloads [url] to [dest], checking [size] and [sha256]. Returns the file path, or throws
  /// [PlatformException] (code CANCELLED, BAD_URL or DOWNLOAD_FAILED).
  static Future<String> download(
    String url,
    String dest, {
    required int size,
    required String sha256,
    required void Function(int done, int total) onProgress,
  }) async {
    if (!isAllowedPackUrl(url)) {
      throw PlatformException(code: 'BAD_URL', message: 'not a release asset of this repo: $url');
    }
    if (_active) {
      throw PlatformException(code: 'BUSY', message: 'a download is already running');
    }
    _active = true;
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'downloadProgress') {
        final m = call.arguments as Map;
        onProgress((m['done'] as num).toInt(), (m['total'] as num).toInt());
      }
    });
    try {
      return (await _ch.invokeMethod<String>(
              'downloadPack', {'url': url, 'dest': dest, 'size': size, 'sha256': sha256})) ??
          dest;
    } finally {
      _active = false;
      _ch.setMethodCallHandler(null);
    }
  }

  static Future<void> cancel() => _ch.invokeMethod('cancelDownload');
}
