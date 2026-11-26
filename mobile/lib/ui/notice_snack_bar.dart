import 'package:flutter/material.dart';

import '../logic/mic_permission.dart';
import '../services/app_settings.dart';

/// Snack bar for a pipeline notice. The microphone-denied notice carries a button that opens the
/// app's system settings, for "don't ask again" (#1928).
SnackBar noticeSnackBar(String text, {Future<bool> Function() openSettings = AppSettings.open}) {
  return SnackBar(
    content: Text(text),
    action: text == MicPermission.deniedText
        ? SnackBarAction(label: '開啟系統設定', onPressed: () => openSettings())
        : null,
  );
}
