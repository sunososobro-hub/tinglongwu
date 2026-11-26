import 'package:flutter/services.dart';

/// Opens this app's page in the phone's system settings (#1928: "don't ask again" for the mic).
class AppSettings {
  static const _ch = MethodChannel('tlwu/native');

  /// False when the settings page could not be opened.
  static Future<bool> open() async {
    try {
      return await _ch.invokeMethod<bool>('openAppSettings') ?? false;
    } catch (_) {
      return false;
    }
  }
}
