import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/services/base_download.dart';
import 'package:mobile/services/model_importer.dart';
import 'package:mobile/ui/message_screen.dart';

// #1863: a fresh install with no models downloads them in the app instead of a "Models missing"
// page. The fake stands in for PackDownloader + ModelImporter; the check uses the real manifest.

final _manifest = ModelManifest.parse(File('assets/models_manifest.json').readAsStringSync());

/// What one file of the downloaded zip turned out to be: (size, sha256).
typedef _Got = (int, String);

class _Fake implements BaseModelSource {
  _Fake(this.inZip);

  /// Target path -> what the zip holds for it.
  Map<String, _Got> inZip;
  final installed = <String>{};
  final urls = <String>[];
  PlatformException? downloadError;
  Completer<void>? hold;
  int cancels = 0;

  @override
  Future<String> download(String url, void Function(int done, int total) onProgress) async {
    urls.add(url);
    onProgress(10 << 20, 100 << 20);
    if (hold != null) await hold!.future;
    if (downloadError != null) throw downloadError!;
    return '/tmp/x.zip';
  }

  /// Like ModelImporter.run: each file is checked with ModelManifest.validate; only OK ones land.
  @override
  Future<ImportReport> install(String path) async {
    final r = ImportReport();
    inZip.forEach((target, got) {
      final v = _manifest.validate(target, got.$1, got.$2);
      if (v.ok) {
        installed.add(target);
        r.imported.add(target);
      } else {
        r.problems.add('$target: ${v.detail}');
      }
    });
    return r;
  }

  @override
  Future<List<String>> missing() async =>
      [for (final f in _manifest.files) if (!installed.contains(f.path)) f.path];

  @override
  Future<void> cancel() async => cancels++;
}

Map<String, _Got> _good() => {for (final f in _manifest.files) f.path: (f.size, f.sha256)};

BaseModelDownload _dl(_Fake f) => BaseModelDownload(f, url: DeviceBaseModelSource.bundleUrl);

void main() {
  test('the download URL is the manifest bundle (a release asset of this repo)', () {
    final j = jsonDecode(File('assets/models_manifest.json').readAsStringSync()) as Map;
    expect(DeviceBaseModelSource.bundleUrl, (j['bundle'] as Map)['url']);
    expect(_manifest.bundleName, DeviceBaseModelSource.zipName);
  });

  test('missing -> download -> every file checked -> done', () async {
    final f = _Fake(_good());
    final d = _dl(f);
    expect(await f.missing(), isNotEmpty);
    final states = <BaseDlState>[];
    d.addListener(() => states.add(d.state));
    expect(await d.run(), isTrue);
    expect(f.urls, [DeviceBaseModelSource.bundleUrl]);
    expect(states, containsAllInOrder([BaseDlState.downloading, BaseDlState.checking, BaseDlState.done]));
    expect(await f.missing(), isEmpty);
    expect(d.error, isNull);
  });

  test('a checksum mismatch is rejected, then 重試 with a good zip installs', () async {
    final bad = _good();
    final llm = _manifest.files.firstWhere((e) => e.group == 'llm');
    bad[llm.path] = (llm.size, 'f' * 64);
    final f = _Fake(bad);
    final d = _dl(f);
    expect(await d.run(), isFalse);
    expect(d.state, BaseDlState.failed);
    expect(d.error, contains('檢查不符'));
    expect(d.error, contains(llm.path));
    expect(f.installed, isNot(contains(llm.path)));
    f.inZip = _good();
    expect(await d.run(), isTrue);
    expect(d.state, BaseDlState.done);
  });

  test('a wrong size is rejected too', () async {
    final bad = _good();
    final vad = _manifest.files.firstWhere((e) => e.group == 'vad');
    bad[vad.path] = (vad.size - 1, vad.sha256);
    final d = _dl(_Fake(bad));
    expect(await d.run(), isFalse);
    expect(d.error, contains(vad.path));
  });

  test('cancel and network failure end in failed with a retry, never done', () async {
    final f = _Fake(_good())..downloadError = PlatformException(code: 'CANCELLED');
    final d = _dl(f);
    expect(await d.run(), isFalse);
    expect(d.error, '下載已停止。');
    f.downloadError = PlatformException(code: 'DOWNLOAD_FAILED', message: 'HTTP 503');
    expect(await d.run(), isFalse);
    expect(d.error, contains('HTTP 503'));
    expect(f.installed, isEmpty);
  });

  test('progress shows MB done / total', () async {
    final f = _Fake(_good())..hold = Completer<void>();
    final d = _dl(f);
    final running = d.run();
    await Future<void>.delayed(Duration.zero);
    expect(d.busy, isTrue);
    expect(d.amount, '10 / 100 MB');
    expect(d.progress, closeTo(0.1, 1e-9));
    expect(await d.run(), isFalse, reason: 'a second start while busy does nothing');
    f.hold!.complete();
    expect(await running, isTrue);
  });

  testWidgets('screen: 開始下載 -> progress + 取消 -> main screen; details folded', (t) async {
    final f = _Fake(_good())..hold = Completer<void>();
    final d = _dl(f);
    var done = 0;
    await t.pumpWidget(MaterialApp(
        home: MissingModelsScreen(
            download: d,
            modelsDir: '/data/models',
            missingFiles: const ['/data/models/silero_vad.onnx'],
            onImport: () {},
            onDone: () => done++)));
    expect(find.textContaining('Models missing'), findsNothing);
    expect(find.text('改用匯入模型'), findsOneWidget);
    expect(find.text('顯示技術細節'), findsOneWidget);
    expect(find.textContaining('silero_vad.onnx'), findsNothing, reason: 'folded');
    await t.tap(find.text('開始下載'));
    await t.pump();
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(find.textContaining('10 / 100 MB'), findsOneWidget);
    await t.tap(find.text('取消'));
    expect(f.cancels, 1);
    f.hold!.complete();
    await t.pumpAndSettle();
    expect(done, 1);
  });

  testWidgets('screen: a rejected file shows the reason and 重試', (t) async {
    final bad = _good();
    final asr = _manifest.files.first;
    bad[asr.path] = (asr.size, '0' * 64);
    final d = _dl(_Fake(bad));
    var done = 0;
    await t.pumpWidget(MaterialApp(
        home: MissingModelsScreen(
            download: d,
            modelsDir: '/data/models',
            missingFiles: const [],
            onImport: () {},
            onDone: () => done++)));
    await t.tap(find.text('開始下載'));
    await t.pumpAndSettle();
    expect(done, 0);
    expect(find.textContaining('檢查不符'), findsOneWidget);
    expect(find.text('重試'), findsOneWidget);
  });
}
