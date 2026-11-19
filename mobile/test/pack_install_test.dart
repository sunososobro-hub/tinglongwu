import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/services/model_importer.dart';
import 'package:mobile/services/pipeline.dart';
import 'package:mobile/ui/pack_install_screen.dart';
import 'package:mobile/ui/subtitle_screen.dart';

const _mb = 1000000;

VoicePack _pack(String id, String lang, int size, {String? kind}) => VoicePack.fromJson({
      'id': id,
      'lang': lang,
      'name': 'tlwu-$id.zip',
      'url': 'https://example.invalid/tlwu-$id.zip',
      'kind': ?kind,
      'files': [
        {'path': '$id/file.bin', 'size': size, 'sha256': 'a' * 64},
      ],
    });

final _asrRu = _pack('lang-ru', 'ru', 70 * _mb, kind: 'asr');
final _voiceRu = _pack('voice-ru', 'ru', 133 * _mb, kind: 'supertonic');
final _voiceEn = _pack('voice-en', 'en', 112 * _mb);

/// Stands in for the browser, the file picker and the importer.
class _Fake implements PackInstaller {
  final opened = <String>[];
  final installed = <String>{};
  bool canOpen = true;
  final downloaded = <String>[];
  Completer<void>? hold; // download waits for this when set
  PlatformException? downloadError;
  int picks = 0;
  int cancels = 0;

  /// What the next import does with the picked names.
  ImportReport Function(List<PickedDoc> docs)? onRun;

  @override
  Future<bool> openUrl(String url) async {
    opened.add(url);
    return canOpen;
  }

  @override
  Future<PickedDoc> download(VoicePack p,
      {required void Function(int done, int total) onProgress}) async {
    downloaded.add(p.id);
    onProgress(p.size ~/ 2, p.size);
    if (hold != null) await hold!.future;
    if (downloadError != null) throw downloadError!;
    return PickedDoc('file:///nonexistent/${p.name}', p.name, p.size);
  }

  @override
  Future<List<PickedDoc>> pick() async {
    picks++;
    return [PickedDoc('content://x', 'picked.zip', 10)];
  }

  @override
  Future<ImportReport> run(List<PickedDoc> docs,
      {required void Function(int done, int total, String name) onProgress}) async {
    onProgress(5, 10, docs.first.name);
    return onRun!(docs);
  }

  @override
  Future<void> cancel() async => cancels++;

  @override
  Future<Set<String>> present() async => {...installed};
}

class _Throwing extends _Fake {
  @override
  Future<PickedDoc> download(VoicePack p,
      {required void Function(int done, int total) onProgress}) async {
    throw const FileSystemException('cannot read the zip');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('com.llfbandit.record/messages'), (call) async => null);

  /// Opens the screen from a button; returns a reader of what it popped with.
  Future<Object? Function()> open(WidgetTester t, _Fake fake, List<VoicePack> packs) async {
    Object? result = 'open';
    await t.pumpWidget(MaterialApp(
        home: Builder(
            builder: (c) => TextButton(
                onPressed: () async => result = await Navigator.of(c).push<bool>(MaterialPageRoute(
                    builder: (_) =>
                        PackInstallScreen(modelsDir: '/m', packs: packs, installer: fake))),
                child: const Text('go')))));
    await t.tap(find.text('go'));
    await t.pumpAndSettle();
    return () => result;
  }

  test('pack titles name the language and the kind', () {
    expect(packTitle(_voiceEn), 'English 朗讀語音包');
    expect(packTitle(_asrRu), 'Русский 辨識包');
  });

  testWidgets('lists only the given packs, with sizes and the mobile-data cost', (t) async {
    final fake = _Fake();
    await open(t, fake, [_asrRu, _voiceRu]);
    expect(find.text('下載辨識包和語音包'), findsOneWidget);
    expect(find.byKey(const Key('pack:lang-ru')), findsOneWidget);
    expect(find.byKey(const Key('pack:voice-ru')), findsOneWidget);
    expect(find.byKey(const Key('pack:voice-en')), findsNothing);
    expect(find.text('下載 (70 MB)'), findsOneWidget);
    expect(find.text('下載 (133 MB)'), findsOneWidget);
    expect(find.textContaining('用行動數據會用掉約 203 MB'), findsOneWidget);
    expect(fake.opened, isEmpty); // nothing downloads without a tap
    expect(fake.downloaded, isEmpty);
  });

  testWidgets('one tap downloads the pack in the app and imports it', (t) async {
    final fake = _Fake();
    fake.onRun = (docs) {
      expect(docs.single.name, 'tlwu-voice-en.zip');
      fake.installed.add('voice-en/file.bin');
      return ImportReport()..imported.add('voice-en/file.bin');
    };
    final result = await open(t, fake, [_voiceEn]);
    expect(find.text('下載語音包'), findsOneWidget);
    await t.tap(find.byKey(const Key('download:voice-en')));
    await t.pumpAndSettle();
    expect(fake.downloaded, ['voice-en']);
    expect(fake.opened, isEmpty); // the browser is not involved
    expect(fake.picks, 0); // and no file picker either
    expect(result(), isTrue);
  });

  testWidgets('download shows progress and cancel stops it', (t) async {
    final fake = _Fake()..hold = Completer<void>();
    await open(t, fake, [_voiceEn]);
    await t.tap(find.byKey(const Key('download:voice-en')));
    await t.pump();
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(find.textContaining('56 MB / 112 MB'), findsOneWidget);
    await t.tap(find.byKey(const Key('cancelImport')));
    expect(fake.cancels, 1);
    fake.downloadError = PlatformException(code: 'CANCELLED');
    fake.hold!.complete();
    await t.pumpAndSettle();
    expect(find.text('已取消'), findsOneWidget);
    expect(find.byKey(const Key('download:voice-en')), findsOneWidget); // can retry
  });

  testWidgets('a failed download says why and the browser fallback still works', (t) async {
    final fake = _Fake()..downloadError = PlatformException(code: 'DOWNLOAD_FAILED', message: 'HTTP 404');
    await open(t, fake, [_voiceEn]);
    await t.tap(find.byKey(const Key('download:voice-en')));
    await t.pumpAndSettle();
    expect(find.textContaining('下載失敗：HTTP 404'), findsOneWidget);
    await t.tap(find.byKey(const Key('browser:voice-en')));
    await t.pumpAndSettle();
    expect(fake.opened, ['https://example.invalid/tlwu-voice-en.zip']);
    expect(find.textContaining('已交給瀏覽器下載'), findsOneWidget);
  });

  testWidgets('an unexpected download error does not leave the screen busy', (t) async {
    final broken = _Throwing();
    await open(t, broken, [_voiceEn]);
    await t.tap(find.byKey(const Key('download:voice-en')));
    await t.pumpAndSettle();
    expect(find.textContaining('下載失敗'), findsOneWidget);
    expect(find.byKey(const Key('cancelImport')), findsNothing);
    expect(t.widget<OutlinedButton>(find.byKey(const Key('download:voice-en'))).onPressed, isNotNull);
  });

  testWidgets('no browser: the URL is shown to copy', (t) async {
    final fake = _Fake()..canOpen = false;
    await open(t, fake, [_voiceEn]);
    await t.tap(find.byKey(const Key('browser:voice-en')));
    await t.pumpAndSettle();
    expect(find.textContaining('https://example.invalid/tlwu-voice-en.zip'), findsOneWidget);
    expect(find.textContaining('已交給瀏覽器下載'), findsNothing);
  });

  test('the device installer calls PackDownloader.download with the pack URL', () async {
    const url = 'https://github.com/sunososobro-hub/tinglongwu/releases/download/mobile-latest/tlwu-voice-en.zip';
    final dir = Directory.systemTemp.createTempSync('pk');
    addTearDown(() => dir.deleteSync(recursive: true));
    final p = VoicePack.fromJson({
      'id': 'voice-en',
      'lang': 'en',
      'name': 'tlwu-voice-en.zip',
      'url': url,
      'files': [
        {'path': 'tts-en/model.onnx', 'size': 4, 'sha256': 'a' * 64},
      ],
    });
    final calls = <MethodCall>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('tlwu/native'), (call) async {
      calls.add(call);
      if (call.method == 'downloadPack') {
        File(call.arguments['dest'] as String)
          ..parent.createSync(recursive: true)
          ..writeAsBytesSync([1, 2, 3, 4, 5]);
        return call.arguments['dest'];
      }
      return null;
    });
    addTearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('tlwu/native'), null));
    final ins = DevicePackInstaller(
        ModelImporter(ModelManifest(files: [], bundleName: '', bundleUrl: ''), dir.path));
    final doc = await ins.download(p, onProgress: (_, _) {});
    final a = calls.single.arguments as Map;
    expect(calls.single.method, 'downloadPack');
    expect(a['url'], url);
    expect(a['dest'], '${dir.path}/.dl/tlwu-voice-en.zip');
    expect(doc.name, 'tlwu-voice-en.zip');
    expect(doc.size, 5);
    expect(doc.uri, startsWith('file:'));
    // A URL outside this repo's releases never reaches the native side.
    final bad = VoicePack.fromJson({
      'id': 'x', 'lang': 'en', 'name': 'x.zip', 'url': 'https://example.invalid/x.zip',
      'files': [{'path': 'x', 'size': 1, 'sha256': 'a' * 64}],
    });
    calls.clear();
    await expectLater(ins.download(bad, onProgress: (_, _) {}),
        throwsA(isA<PlatformException>().having((e) => e.code, 'code', 'BAD_URL')));
    expect(calls, isEmpty);
  });

  testWidgets('importing the pack closes the screen with true', (t) async {
    final fake = _Fake();
    fake.onRun = (_) {
      fake.installed.add('voice-en/file.bin');
      return ImportReport()..imported.add('voice-en/file.bin');
    };
    final result = await open(t, fake, [_voiceEn]);
    await t.tap(find.byKey(const Key('importDownloaded')));
    await t.pumpAndSettle();
    expect(fake.picks, 1);
    expect(result(), isTrue);
    expect(find.text('go'), findsOneWidget);
  });

  testWidgets('a wrong file is reported and nothing is installed', (t) async {
    final fake = _Fake();
    fake.onRun = (_) => ImportReport()..problems.add('picked.zip: SHA-256 differs');
    final result = await open(t, fake, [_voiceEn]);
    await t.tap(find.byKey(const Key('importDownloaded')));
    await t.pumpAndSettle();
    expect(find.text('✗ picked.zip: SHA-256 differs'), findsOneWidget);
    expect(find.byKey(const Key('importDownloaded')), findsOneWidget); // try again
    await t.tap(find.byType(BackButton));
    await t.pumpAndSettle();
    expect(result(), isFalse); // the pipeline is not restarted
  });

  testWidgets('one of two packs imported: stays open and says what is left', (t) async {
    final fake = _Fake();
    fake.onRun = (_) {
      fake.installed.add('lang-ru/file.bin');
      return ImportReport()..imported.add('lang-ru/file.bin');
    };
    // Two cards now push the import button below the fold of a default-size list: use a tall view.
    await t.binding.setSurfaceSize(const Size(800, 2400));
    addTearDown(() => t.binding.setSurfaceSize(null));
    final result = await open(t, fake, [_asrRu, _voiceRu]);
    await t.tap(find.byKey(const Key('importDownloaded')));
    await t.pumpAndSettle();
    expect(find.text('已安裝'), findsOneWidget);
    expect(find.text('已匯入一部分，還缺：Русский 朗讀語音包'), findsOneWidget);
    expect(find.byKey(const Key('download:lang-ru')), findsNothing);
    expect(find.byKey(const Key('download:voice-ru')), findsOneWidget);
    // System back still reports the import, so the recognizer is picked up.
    await t.binding.handlePopRoute();
    await t.pumpAndSettle();
    expect(result(), isTrue);
  });

  testWidgets('the screen chip asks for exactly the packs the language is missing', (t) async {
    final asked = <List<String>>[];
    var general = 0;
    final p = Pipeline()
      ..stage = Stage.listening
      ..packs = [_voiceEn, _asrRu, _voiceRu]
      ..conversation = const Conversation(enabled: true, foreign: 'ru');
    await t.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => t.binding.setSurfaceSize(null));
    await t.pumpWidget(MaterialApp(
        home: ListenableBuilder(
            listenable: p,
            builder: (_, _) => SubtitleScreen(
                pipeline: p,
                onImport: () => general++,
                onInstall: (packs) => asked.add([for (final x in packs) x.id])))));
    await t.tap(find.byKey(const Key('packAction')));
    await t.pumpAndSettle();
    expect(asked, [
      ['lang-ru', 'voice-ru']
    ]);
    // From the sheet, after switching to English: the English voice only.
    await t.tap(find.byKey(const Key('pairPill')));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const Key('partner:en')));
    await t.pumpAndSettle();
    await t.ensureVisible(find.byKey(const Key('sheetDownload')));
    await t.tap(find.byKey(const Key('sheetDownload')));
    await t.pumpAndSettle();
    expect(asked.last, ['voice-en']);
    expect(general, 0);
  });
}
