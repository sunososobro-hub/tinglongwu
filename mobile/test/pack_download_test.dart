import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/pack_download.dart';

// First-run Play download screen (#1031): what it says in each state.
void main() {
  const mb = 1 << 20;

  test('downloading: progress bar, MB done / total, no buttons', () {
    final v = PackDownloadView.of(done: 300 * mb, total: 1200 * mb, state: 'downloading');
    expect(v.progress, closeTo(0.25, 1e-9));
    expect(v.amount, '300 / 1200 MB');
    expect(v.status, '下載中…');
    expect(v.retry, isFalse);
    expect(v.mobileData, isFalse);
  });

  test('total not known yet: indeterminate bar, no MB line', () {
    final v = PackDownloadView.of(done: 0, total: 0, state: 'pending');
    expect(v.progress, isNull);
    expect(v.amount, '');
    expect(v.hint, contains('網路'));
  });

  test('waiting for Wi-Fi offers mobile data', () {
    final v = PackDownloadView.of(done: 0, total: 1376 * mb, state: 'waitingWifi');
    expect(v.mobileData, isTrue);
    expect(v.status, contains('Wi-Fi'));
    expect(v.retry, isFalse);
  });

  test('no network / storage / other failure: retry, plain zh-TW reason', () {
    final n = PackDownloadView.of(done: 10 * mb, total: 100 * mb, state: 'failed', error: 'network');
    expect(n.retry, isTrue);
    expect(n.status, '沒有網路，無法下載。');
    expect(n.amount, '10 / 100 MB');
    expect(PackDownloadView.of(done: 0, total: 0, state: 'failed', error: 'storage').status,
        contains('空間不足'));
    expect(PackDownloadView.of(done: 0, total: 0, state: 'pending', error: '-2').status,
        contains('錯誤 -2'));
    expect(PackDownloadView.of(done: 0, total: 0, state: 'pending', error: 'cancelled').retry,
        isTrue);
  });

  test('title is the plain zh-TW line', () {
    expect(PackDownloadView.title, '正在下載翻譯需要的檔案（第一次使用才需要）');
  });
}
