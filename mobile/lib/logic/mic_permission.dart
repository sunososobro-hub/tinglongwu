/// Asks for the microphone (RECORD_AUDIO) only when the user starts something that records (#1928).
/// Nothing here runs at app open: the first `ensure()` comes from the start button.
class MicPermission {
  MicPermission({required this.isGranted, required this.request, this.onRationale});

  /// Reads the current state without showing the system dialog.
  final Future<bool> Function() isGranted;

  /// Shows the system dialog (or answers at once when already granted / "don't ask again").
  final Future<bool> Function() request;

  /// Called with [rationale] right before the system dialog.
  final void Function(String text)? onRationale;

  static const rationale = '要開始聽了，需要用到錄音權限。聲音只在你的手機上處理，不會上傳。';
  static const deniedText = '沒有錄音權限，無法開始。再按一次開始鈕可重新授權；若系統不再詢問，請到手機設定 > 應用程式 > 聽龍 > 權限開啟麥克風。';

  bool _granted = false;

  /// Number of system-dialog requests so far (tests).
  int requests = 0;

  Future<bool> ensure() async {
    if (_granted) return true;
    if (await isGranted()) return _granted = true;
    onRationale?.call(rationale);
    requests++;
    return _granted = await request();
  }
}
