import 'dart:io' show Platform;

import 'package:flutter/services.dart';

import '../logic/device_probe.dart';

/// Reads the device facts from the native side (#1065); falls back to what Dart knows
/// (core count) where there is no native handler (iOS, tests).
class DeviceProbeService {
  static const _ch = MethodChannel('tlwu/native');

  static Future<DeviceInfo> collect() async {
    try {
      final m = await _ch.invokeMapMethod<String, Object?>('deviceInfo');
      if (m != null) return DeviceInfo.fromJson(m);
    } catch (_) {}
    return DeviceInfo(cores: Platform.numberOfProcessors);
  }
}
