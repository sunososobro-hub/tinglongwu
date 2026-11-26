import 'package:flutter/material.dart';

import '../logic/mic_permission.dart';
import '../services/app_settings.dart';
import 'a11y.dart';

/// Snack bar for a pipeline notice. The microphone-denied notice carries a button that opens the
/// app's system settings, for "don't ask again" (#1928).
SnackBar noticeSnackBar(String text,
    {Future<bool> Function() openSettings = AppSettings.open, VoidCallback? onRetry}) {
  final denied = text == MicPermission.deniedText;
  return SnackBar(
    // Announced by TalkBack when it appears; the denied line also carries a retry button (#1963).
    content: LiveRegion(
      label: text,
      child: denied && onRetry != null
          ? Row(children: [
              Expanded(child: ExcludeSemantics(child: Text(text))),
              TextButton(onPressed: onRetry, child: const Text(micRetryLabel)),
            ])
          : ExcludeSemantics(child: Text(text)),
    ),
    action: text == MicPermission.deniedText
        ? SnackBarAction(label: '開啟系統設定', onPressed: () => openSettings())
        : null,
  );
}
