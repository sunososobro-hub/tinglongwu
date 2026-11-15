import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/phone_audio.dart';
import 'package:mobile/services/playback_capture.dart';

/// 看影片翻譯 on iOS (#1076): mode availability per platform and the `tlwu/native` wiring that
/// TlwuNativeChannel.swift answers (same method names as Android's MainActivity.kt).
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const native = MethodChannel('tlwu/native');
  final calls = <MethodCall>[];
  bool nativeSupported = true;

  setUp(() {
    calls.clear();
    nativeSupported = true;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(native,
        (call) async {
      calls.add(call);
      switch (call.method) {
        case 'playbackCaptureSupported':
          return nativeSupported;
        case 'startPlaybackCapture':
        case 'overlayAllowed':
          return true;
      }
      return null;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(native, null);
  });

  group('CapturePlatform', () {
    test('Android and iOS may capture, other platforms never ask native', () {
      expect(CapturePlatform.android.mayCapture, isTrue);
      expect(CapturePlatform.ios.mayCapture, isTrue);
      expect(CapturePlatform.other.mayCapture, isFalse);
    });

    test('only iOS explains before the system sheet; only Android has opacity', () {
      expect(CapturePlatform.ios.explainsBeforeStart, isTrue);
      expect(CapturePlatform.android.explainsBeforeStart, isFalse);
      expect(CapturePlatform.android.overlayOpacityAdjustable, isTrue);
      expect(CapturePlatform.ios.overlayOpacityAdjustable, isFalse);
    });

    test('unsupported message names the right OS version', () {
      expect(CapturePlatform.ios.unsupportedText, contains('iOS 15'));
      expect(CapturePlatform.android.unsupportedText, contains('Android 10'));
      expect(CapturePlatform.ios.overlayTitle, isNot(CapturePlatform.android.overlayTitle));
    });

    test('the test host is neither Android nor iOS', () {
      expect(CapturePlatform.current(), CapturePlatform.other);
    });
  });

  group('PlaybackCapture channel wiring', () {
    test('iOS asks the native side and returns its answer', () async {
      expect(await PlaybackCapture.supported(platform: CapturePlatform.ios), isTrue);
      expect(calls.single.method, 'playbackCaptureSupported');
      nativeSupported = false; // e.g. App Group missing in an unsigned build
      expect(await PlaybackCapture.supported(platform: CapturePlatform.ios), isFalse);
    });

    test('other platforms answer false without a channel call', () async {
      expect(await PlaybackCapture.supported(platform: CapturePlatform.other), isFalse);
      expect(await PlaybackCapture.supported(), isFalse);
      expect(calls, isEmpty);
    });

    test('a channel error counts as unsupported', () async {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(native, (call) async => throw PlatformException(code: 'X'));
      expect(await PlaybackCapture.supported(platform: CapturePlatform.ios), isFalse);
    });

    test('start / overlay calls use the shared method names and argument shapes', () async {
      expect(await PlaybackCapture.start(), isTrue);
      await PlaybackCapture.overlayStyle(const OverlayStyle(fontSize: 24, opacity: 0.5));
      await PlaybackCapture.overlayShow();
      await PlaybackCapture.overlayLines(
        const [SubtitleLine(source: 'hello', translation: '你好'), SubtitleLine(translation: '世界')],
        null,
        draft: 'wor',
      );
      await PlaybackCapture.overlayHide();
      await PlaybackCapture.stop();
      expect(calls.map((c) => c.method), [
        'startPlaybackCapture',
        'overlayStyle',
        'overlayShow',
        'overlayText',
        'overlayHide',
        'stopPlaybackCapture',
      ]);
      expect(calls[1].arguments, {'fontSize': 24.0, 'opacity': 0.5});
      expect(calls[3].arguments, {
        'lines': [
          {'src': 'hello', 'tr': '你好'},
          {'src': null, 'tr': '世界'},
        ],
        'hint': null,
        'draft': 'wor',
        'draftTr': '',
      });
    });
  });

  test('iOS start text warns that DRM apps may give silence', () {
    expect(CaptureModeText.iosStartExplain, contains('Netflix'));
    expect(CaptureModeText.iosStartExplain, contains('聽龍字幕')); // the extension's display name
  });
}
