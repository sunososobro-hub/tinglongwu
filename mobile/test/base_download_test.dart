import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/first_use_plan.dart';
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
  final unmeteredFlags = <bool>[]; // #1879: the flag of each base / pack download
  PlatformException? downloadError;
  Completer<void>? hold;
  int cancels = 0;

  @override
  Future<String> download(String url, void Function(int done, int total) onProgress,
      {bool unmeteredOnly = false}) async {
    urls.add(url);
    unmeteredFlags.add(unmeteredOnly);
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

  // Optional packs (#1971): the zip of pack p is "/tmp/<id>.zip"; packZip says what it holds.
  final packZip = <String, _Got>{}; // pack id -> (size, sha256) of its first file
  final packUrls = <String>[];
  final packsDone = <String>{}; // ids of installed packs

  @override
  Future<String> downloadPack(VoicePack p, void Function(int done, int total) onProgress,
      {bool unmeteredOnly = false}) async {
    packUrls.add(p.url);
    unmeteredFlags.add(unmeteredOnly);
    onProgress(p.size ~/ 2, p.size);
    return '/tmp/${p.id}.zip';
  }

  @override
  Future<ImportReport> installPack(VoicePack p, String path) async {
    final r = ImportReport();
    final got = packZip[p.id] ?? (p.files.first.size, p.files.first.sha256);
    final e = p.files.first;
    if (got.$1 == e.size && got.$2 == e.sha256) {
      packsDone.add(p.id);
      r.imported.add(e.path);
    } else {
      r.problems.add('${e.path}: sha256 mismatch');
    }
    return r;
  }

  @override
  Future<bool> packInstalled(VoicePack p) async => packsDone.contains(p.id);

  @override
  Future<List<String>> missing() async =>
      [for (final f in _manifest.files) if (!installed.contains(f.path)) f.path];

  @override
  Future<void> cancel() async => cancels++;
}

Map<String, _Got> _good() => {for (final f in _manifest.files) f.path: (f.size, f.sha256)};

/// #1864: a fake network probe; counts how often it was asked.
class _Net implements NetworkProbe {
  _Net(this.k);
  final NetKind k;
  int asked = 0;

  @override
  Future<NetKind> kind() async {
    asked++;
    return k;
  }
}

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

  group('first-use plan (#1971)', () {
    VoicePack pk(String id, String lang, {String kind = 'vits', int size = 100}) =>
        VoicePack.fromJson({
          'id': id,
          'lang': lang,
          'kind': kind,
          'name': '$id.zip',
          'url': 'https://example.invalid/$id.zip',
          'files': [
            {'path': '${kind == 'asr' ? 'asr' : 'tts'}-$lang/model.onnx', 'size': size, 'sha256': 'a' * 64}
          ],
        });
    final packs = [pk('voice-en', 'en', size: 30), pk('asr-ru', 'ru', kind: 'asr', size: 70)];
    List<String> ids(FirstUsePlan p) => [for (final i in p.items) i.id];

    test('default choice is exactly the base zip', () {
      final p = firstUsePlan(const FirstUseChoice(), packs, baseBytes: 1000);
      expect(ids(p), ['base']);
      expect(p.totalBytes, 1000);
      // a language alone, without voice / conversation, adds nothing
      expect(ids(firstUsePlan(const FirstUseChoice(otherLang: 'en'), packs, baseBytes: 1000)), ['base']);
    });

    test('each choice adds exactly its pack and its bytes', () {
      final v = firstUsePlan(const FirstUseChoice(voice: true, otherLang: 'en'), packs, baseBytes: 1000);
      expect(ids(v), ['base', 'voice-en']);
      expect(v.totalBytes, 1030);
      final c = firstUsePlan(const FirstUseChoice(conversation: true, otherLang: 'ru'), packs, baseBytes: 1000);
      expect(ids(c), ['base', 'asr-ru']);
      expect(c.totalBytes, 1070);
    });

    test('a language with no pack adds nothing; no duplicates', () {
      expect(ids(firstUsePlan(const FirstUseChoice(voice: true, conversation: true, otherLang: 'de'), packs, baseBytes: 1)), ['base']);
      final both = firstUsePlan(const FirstUseChoice(voice: true, conversation: true, otherLang: 'en'),
          [...packs, pk('voice-en', 'en', size: 30)], baseBytes: 1);
      expect(ids(both), ['base', 'voice-en']);
    });

    test('run() downloads the base zip, then each pack; total covers the plan', () async {
      final f = _Fake(_good());
      final d = BaseModelDownload(f, url: DeviceBaseModelSource.bundleUrl, extraPacks: packs);
      expect(await d.run(), isTrue);
      expect(f.urls, [DeviceBaseModelSource.bundleUrl]);
      expect(f.packUrls, ['https://example.invalid/voice-en.zip', 'https://example.invalid/asr-ru.zip']);
      expect(d.total, (100 << 20) + 100);
      expect(d.done, d.total);
    });

    test('a checksum mismatch in a pack fails the run; 重試 skips installed files', () async {
      final f = _Fake(_good())..packZip['asr-ru'] = (70, 'f' * 64);
      final d = BaseModelDownload(f, url: DeviceBaseModelSource.bundleUrl, extraPacks: packs);
      expect(await d.run(), isFalse);
      expect(d.state, BaseDlState.failed);
      expect(d.error, contains('檢查不符'));
      expect(f.packsDone, {'voice-en'});
      f.packZip.clear();
      f.packUrls.clear();
      expect(await d.run(), isTrue);
      expect(f.packUrls, ['https://example.invalid/asr-ru.zip'], reason: 'voice-en is already installed');
    });
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
    // #1927: one plain line on why the models are a separate download.
    expect(find.text('這些檔案會存在你的手機裡。之後更新 App 只會下載程式本身，不用再下載一次，所以更新很快。'),
        findsOneWidget);
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

  // #1864 CodeRabbit: only unmetered Wi-Fi / Ethernet counts as unmetered; 5G marked NOT_METERED still asks.
  group('netKindFrom', () {
    Map<String, bool> m({bool i = true, bool nm = false, bool w = false, bool e = false, bool c = false}) =>
        {'internet': i, 'notMetered': nm, 'wifi': w, 'ethernet': e, 'cellular': c};
    test('unmetered Wi-Fi', () => expect(netKindFrom(m(nm: true, w: true)), NetKind.unmetered));
    test('unmetered Ethernet', () => expect(netKindFrom(m(nm: true, e: true)), NetKind.unmetered));
    test('unmetered cellular (5G) still asks', () => expect(netKindFrom(m(nm: true, c: true)), NetKind.metered));
    test('Wi-Fi + cellular transport asks', () => expect(netKindFrom(m(nm: true, w: true, c: true)), NetKind.metered));
    test('metered Wi-Fi (hotspot) asks', () => expect(netKindFrom(m(w: true)), NetKind.metered));
    test('unmetered with no known transport (VPN only) asks', () => expect(netKindFrom(m(nm: true)), NetKind.metered));
    test('no internet', () => expect(netKindFrom(m(i: false, nm: true, w: true)), NetKind.none));
    test('null / malformed', () {
      expect(netKindFrom(null), NetKind.unknown);
      expect(netKindFrom('unmetered'), NetKind.unknown);
    });
  });

  // #1864: Wi-Fi starts by itself; mobile data asks first.
  test('auto start: on Wi-Fi (unmetered) the download starts with no tap', () async {
    final f = _Fake(_good());
    final d = _dl(f);
    final net = _Net(NetKind.unmetered);
    expect(await d.autoStart(net), isTrue);
    expect(f.urls, [DeviceBaseModelSource.bundleUrl]);
    expect(d.state, BaseDlState.done);
    expect(d.onMobileData, isFalse);
    expect(await d.autoStart(net), isFalse, reason: 'only the first call does anything');
    expect(net.asked, 1);
    expect(f.urls, hasLength(1));
  });

  for (final k in [NetKind.metered, NetKind.none, NetKind.unknown]) {
    test('auto start: on ${k.name} nothing is fetched', () async {
      final f = _Fake(_good());
      final d = _dl(f);
      expect(await d.autoStart(_Net(k)), isFalse);
      expect(f.urls, isEmpty);
      expect(d.state, BaseDlState.idle);
      expect(d.net, k);
      expect(d.onMobileData, k == NetKind.metered);
    });
  }

  testWidgets('screen: on mobile data it asks, fetches only after 用行動數據下載', (t) async {
    final f = _Fake(_good());
    final d = _dl(f);
    var done = 0;
    await t.pumpWidget(MaterialApp(
        home: MissingModelsScreen(
            download: d,
            modelsDir: '/data/models',
            missingFiles: const [],
            onImport: () {},
            onDone: () => done++)));
    expect(await d.autoStart(_Net(NetKind.metered)), isFalse);
    await t.pump();
    expect(f.urls, isEmpty, reason: 'nothing fetched before the tap');
    expect(find.text('開始下載'), findsNothing);
    expect(find.text('用行動數據下載（約 1.3 GB）'), findsOneWidget);
    await t.tap(find.text('用行動數據下載（約 1.3 GB）'));
    await t.pumpAndSettle();
    expect(f.urls, [DeviceBaseModelSource.bundleUrl]);
    expect(done, 1);
  });

  testWidgets('screen: on Wi-Fi the download is already running, no tap', (t) async {
    final f = _Fake(_good())..hold = Completer<void>();
    final d = _dl(f);
    await t.pumpWidget(MaterialApp(
        home: MissingModelsScreen(
            download: d,
            modelsDir: '/data/models',
            missingFiles: const [],
            onImport: () {},
            onDone: () {})));
    final started = d.autoStart(_Net(NetKind.unmetered));
    await t.pump();
    expect(f.urls, [DeviceBaseModelSource.bundleUrl]);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(find.textContaining('用行動數據'), findsNothing);
    f.hold!.complete();
    await t.pumpAndSettle();
    expect(await started, isTrue);
  });

  // #1879: the automatic download is bound to the unmetered network; a tapped one is not.
  test('auto start asks the native side for an unmetered-only download', () async {
    final f = _Fake(_good());
    expect(await _dl(f).autoStart(_Net(NetKind.unmetered)), isTrue);
    expect(f.unmeteredFlags, [true]);
  });

  test('tapped download (run) keeps the default network', () async {
    final f = _Fake(_good());
    expect(await _dl(f).run(), isTrue);
    expect(f.unmeteredFlags, [false]);
  });

  test('bound network lost: the auto download fails with a zh-TW error, nothing else is fetched', () async {
    final f = _Fake(_good())
      ..downloadError = PlatformException(code: 'NETWORK_LOST', message: 'Wi-Fi 已中斷，自動下載已停止');
    final d = _dl(f);
    expect(await d.autoStart(_Net(NetKind.unmetered)), isFalse);
    expect(d.state, BaseDlState.failed);
    expect(d.error, contains('Wi-Fi 已中斷'));
    expect(f.urls.length, 1, reason: 'no retry on another network');
  });

  test('network lost mid-download (after progress): failed with the zh-TW message, no mobile-data retry', () async {
    final f = _Fake(_good())
      ..downloadError = PlatformException(code: 'NETWORK_LOST', message: 'Wi-Fi 已中斷，自動下載已停止（不會改用行動數據）。');
    final d = _dl(f);
    expect(await d.autoStart(_Net(NetKind.unmetered)), isFalse);
    expect(d.done, greaterThan(0), reason: 'progress had started');
    expect(d.error, contains('不會改用行動數據'));
    expect(f.unmeteredFlags, [true]);
  });

  // #1972: the first-use choice block; sizes come from the packs given to the screen.
  group('first-use choices (#1972)', () {
    VoicePack pk(String id, String lang, {String kind = 'vits', required int size}) =>
        VoicePack.fromJson({
          'id': id,
          'lang': lang,
          'kind': kind,
          'name': '$id.zip',
          'url': 'https://example.invalid/$id.zip',
          'files': [
            {'path': '${kind == 'asr' ? 'asr' : 'tts'}-$lang/model.onnx', 'size': size, 'sha256': 'a' * 64}
          ],
        });
    final packs = [
      pk('voice-en', 'en', size: 118000000),
      pk('asr-ru', 'ru', kind: 'asr', size: 200000000),
    ];
    const base = 1300000000;

    Future<(BaseModelDownload, _Fake)> pump(WidgetTester t) async {
      final f = _Fake(_good());
      final d = _dl(f);
      await t.pumpWidget(MaterialApp(
          home: MissingModelsScreen(
              download: d,
              modelsDir: '/data/models',
              missingFiles: const [],
              packs: packs,
              baseBytes: base,
              onImport: () {},
              onDone: () {})));
      return (d, f);
    }

    testWidgets('defaults: watch only, base size on the button, languages with a pack listed', (t) async {
      await pump(t);
      expect(find.text('看影片／聽別人說（只要字幕）'), findsOneWidget);
      expect(find.text('對話翻譯（我也要說話）'), findsOneWidget);
      expect(find.text('朗讀語音（+118 MB）'), findsOneWidget);
      expect(find.text('開始下載（1.30 GB）'), findsOneWidget);
      expect(find.byKey(const Key('lang-ru')), findsOneWidget);
      expect(find.byKey(const Key('lang-en')), findsOneWidget);
      expect(find.byKey(const Key('lang-ja')), findsNothing, reason: 'no pack for ja');
    });

    testWidgets('ticking 對話翻譯 + 朗讀語音 + a language adds exactly those packs', (t) async {
      final (d, f) = await pump(t);
      await t.tap(find.byKey(const Key('lang-ru')));
      await t.pump();
      expect(find.text('開始下載（1.30 GB）'), findsOneWidget, reason: 'language alone adds nothing');
      await t.tap(find.byKey(const Key('choiceConversation')));
      await t.pump();
      expect(find.text('開始下載（1.50 GB）'), findsOneWidget); // + asr-ru 200 MB
      await t.tap(find.byKey(const Key('choiceVoice')));
      await t.pump();
      // No ru voice pack in this manifest: still only the ASR pack.
      expect(find.text('開始下載（1.50 GB）'), findsOneWidget);
      await t.tap(find.byKey(const Key('lang-en')));
      await t.pump();
      expect(find.text('開始下載（1.42 GB）'), findsOneWidget); // + voice-en 118 MB (en has no ASR pack)
      await t.tap(find.byKey(const Key('choiceWatch')));
      await t.pump();
      expect(find.text('開始下載（1.42 GB）'), findsOneWidget);
      await t.tap(find.text('開始下載（1.42 GB）'));
      await t.pumpAndSettle();
      expect(d.extraPacks.map((p) => p.id), ['voice-en']);
      expect(f.packUrls, ['https://example.invalid/voice-en.zip']);
    });

    testWidgets('no packs: the plain screen with today\'s 開始下載', (t) async {
      await t.pumpWidget(MaterialApp(
          home: MissingModelsScreen(
              download: _dl(_Fake(_good())),
              modelsDir: '/data/models',
              missingFiles: const [],
              onImport: () {},
              onDone: () {})));
      expect(find.text('開始下載'), findsOneWidget);
      expect(find.text('看影片／聽別人說（只要字幕）'), findsNothing);
    });
  });
}
