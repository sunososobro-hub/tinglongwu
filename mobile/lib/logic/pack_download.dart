/// What the first-run download screen says (#1031), from the Play pack state. Pure, so it is
/// unit-tested; the screen only lays it out.
class PackDownloadView {
  const PackDownloadView({
    required this.status,
    this.hint = '',
    this.progress,
    this.amount = '',
    this.retry = false,
    this.mobileData = false,
  });

  static const title = '正在下載翻譯需要的檔案（第一次使用才需要）';

  final String status;
  final String hint;

  /// 0..1, or null for an indeterminate bar (total not known yet). Not shown on failure.
  final double? progress;

  /// "123 / 1376 MB", empty while the total is not known.
  final String amount;

  /// Show 「重試」.
  final bool retry;

  /// Show 「改用行動數據下載」 (Play waits for Wi-Fi).
  final bool mobileData;

  /// [state]: pending, downloading, transferring, waitingWifi, failed (PlayPacks.kt stateName).
  /// [error]: null while running; else network, storage, cancelled or a Play error code.
  factory PackDownloadView.of(
      {required int done, required int total, required String state, String? error}) {
    final amount = total > 0 ? '${_mb(done)} / ${_mb(total)} MB' : '';
    final frac = total > 0 ? (done / total).clamp(0.0, 1.0) : null;
    if (error != null) {
      return PackDownloadView(
        status: switch (error) {
          'network' => '沒有網路，無法下載。',
          'storage' => '手機空間不足，無法下載。',
          'cancelled' => '下載已停止。',
          _ => '下載失敗（Google Play 錯誤 $error）。',
        },
        hint: switch (error) {
          'network' => '請連上 Wi-Fi 或行動數據，再按「重試」。',
          'storage' => '需要約 1.4 GB 空間。清出空間後按「重試」。',
          _ => '按「重試」再試一次。',
        },
        amount: amount,
        retry: true,
      );
    }
    return switch (state) {
      'waitingWifi' => PackDownloadView(
          status: '檔案較大（約 1.4 GB），正在等 Wi-Fi。',
          hint: '連上 Wi-Fi 就會自動繼續。也可以改用行動數據下載。',
          progress: frac,
          amount: amount,
          mobileData: true,
        ),
      'downloading' => PackDownloadView(
          status: '下載中…',
          hint: '由 Google Play 下載，本 app 不連網。可以先切到別的 app，下載會繼續。',
          progress: frac,
          amount: amount,
        ),
      'transferring' =>
        PackDownloadView(status: '快好了，正在安裝檔案…', progress: frac, amount: amount),
      _ => PackDownloadView(
          status: '等待 Google Play 開始下載…',
          hint: '請確認手機有連上網路。',
          progress: frac,
          amount: amount,
        ),
    };
  }

  static String _mb(int b) => (b / (1 << 20)).toStringAsFixed(0);
}
