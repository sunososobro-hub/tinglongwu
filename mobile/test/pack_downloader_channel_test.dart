import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/pack_downloader.dart';

// #1879: only an automatic download sends unmeteredOnly; the native side then binds every
// connection to the unmetered network and answers NETWORK_LOST when none is up.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const url = 'https://github.com/sunososobro-hub/tinglongwu/releases/download/mobile-latest/x.zip';
  final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  tearDown(() => messenger.setMockMethodCallHandler(const MethodChannel('tlwu/native'), null));

  test('unmeteredOnly is sent only when asked', () async {
    final args = <Map>[];
    messenger.setMockMethodCallHandler(const MethodChannel('tlwu/native'), (call) async {
      args.add(call.arguments as Map);
      return '/tmp/x.zip';
    });
    await PackDownloader.download(url, '/tmp/x.zip', size: -1, sha256: '', onProgress: (_, _) {});
    await PackDownloader.download(url, '/tmp/x.zip',
        size: -1, sha256: '', onProgress: (_, _) {}, unmeteredOnly: true);
    expect(args[0].containsKey('unmeteredOnly'), isFalse);
    expect(args[1]['unmeteredOnly'], isTrue);
  });

  test('NETWORK_LOST from the native side reaches the caller', () async {
    messenger.setMockMethodCallHandler(const MethodChannel('tlwu/native'), (call) async {
      throw PlatformException(code: 'NETWORK_LOST', message: 'Wi-Fi 已中斷');
    });
    await expectLater(
        PackDownloader.download(url, '/tmp/x.zip',
            size: -1, sha256: '', onProgress: (_, _) {}, unmeteredOnly: true),
        throwsA(isA<PlatformException>().having((e) => e.code, 'code', 'NETWORK_LOST')));
  });
}
