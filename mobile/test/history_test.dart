import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/feature_prefs.dart';
import 'package:mobile/logic/history.dart';
import 'package:mobile/services/history_store.dart';
import 'package:mobile/services/pipeline.dart';
import 'package:mobile/ui/history_screen.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  // Pipeline() creates a `record` AudioRecorder; stub its platform channel.
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('com.llfbandit.record/messages'), (call) async => null);

  late Directory tmp;
  late HistoryStore store;
  var clock = 1000;

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('tlwu_hist_');
    store = HistoryStore('${tmp.path}/history');
    clock = 1000;
  });
  tearDown(() async {
    await Future<void>.delayed(const Duration(milliseconds: 50)); // queued appends finish
    try {
      tmp.deleteSync(recursive: true);
    } catch (_) {}
  });

  /// Lets real file IO finish and the fake-async widget tree see it: every IO step needs a
  /// real-time wait, then a pump to run what it scheduled.
  Future<void> settle(WidgetTester t) async {
    for (var i = 0; i < 30; i++) {
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 15)));
      await t.pump(const Duration(milliseconds: 20));
    }
    await t.pumpAndSettle();
  }

  HistoryRecorder recorder({bool enabled = true}) =>
      HistoryRecorder(store, enabled: enabled, nowMs: () => clock += 10);

  group('format', () {
    test('a session round-trips: header, lines, end', () {
      final text = HistoryFormat.header(100, HistoryMode.conversation, 'zh') +
          HistoryFormat.line(const HistoryLine(
              atMs: 120, src: 'Hello', tr: '你好', lang: 'en', tgt: 'zh', side: 1)) +
          HistoryFormat.line(const HistoryLine(
              atMs: 150, src: '你好嗎', tr: 'How are you?', lang: 'zh', tgt: 'en', side: 0)) +
          HistoryFormat.end(200);
      final s = HistoryFormat.parse('100', text)!;
      expect(s.startMs, 100);
      expect(s.endMs, 200);
      expect(s.mode, HistoryMode.conversation);
      expect(s.lines, hasLength(2));
      expect(s.lines[0].side, 1);
      expect(s.lines[1].tr, 'How are you?');
      expect(s.preview, '你好');
      expect(s.languageLabel, '中文 ⇄ 英語');
    });

    test('a crashed session (no end record, half-written last line) still loads', () {
      final text = [
        HistoryFormat.header(100, HistoryMode.speak, 'zh'),
        HistoryFormat.line(const HistoryLine(
            atMs: 130, src: 'Привет', tr: '你好', lang: 'ru', tgt: 'zh')),
        '{"t":"l","at":140,"src":"Как де',
      ].join();
      final s = HistoryFormat.parse('100', text)!;
      expect(s.lines, hasLength(1));
      expect(s.endMs, 130); // last line's time
      expect(s.languageLabel, '俄語 → 中文');
    });

    test('no header = not a session; unknown modes fall back; draft never appears in the format', () {
      expect(HistoryFormat.parse('1', 'garbage\n{"t":"l","at":1,"src":"a","tr":"b"}\n'), isNull);
      expect(HistoryMode.parse('nope'), HistoryMode.speak);
      expect(HistoryFormat.line(const HistoryLine(atMs: 1, src: 'a', tr: 'b')),
          isNot(contains('draft')));
    });

    test('a pending line keeps its original when there is no translation', () {
      const l = HistoryLine(atMs: 1, src: 'only source', tr: '');
      final s = HistoryFormat.parse('1', HistoryFormat.header(1, HistoryMode.video, 'zh') + HistoryFormat.line(l))!;
      expect(s.preview, 'only source');
      expect(s.mode.label, '看影片翻譯');
    });
  });

  group('recorder and store', () {
    test('lines are on disk as they finalize, before the session ends (crash-safe)', () async {
      final r = recorder();
      r.begin(HistoryMode.speak, 'zh');
      r.add(src: 'one', tr: '一', lang: 'en', tgt: 'zh');
      r.add(src: 'two', tr: '二', lang: 'en', tgt: 'zh');
      final id = r.activeId!;
      // Wait for the queued appends (no end record yet).
      for (var i = 0; i < 50 && !File('${store.dir}/$id.jsonl').readAsStringSync().contains('two'); i++) {
        await Future<void>.delayed(const Duration(milliseconds: 10));
      }
      final s = (await store.list()).firstWhere((x) => x.id == id);
      expect(s.lines.map((l) => l.src), ['one', 'two']);
      await r.end();
      expect((await store.list()).firstWhere((x) => x.id == id).endMs, greaterThan(0));
    });

    test('a session without a finalized line leaves no file', () async {
      final r = recorder();
      r.begin(HistoryMode.speak, 'zh');
      r.add(src: '  ', tr: '');
      await r.end();
      expect(Directory(store.dir).existsSync() ? Directory(store.dir).listSync() : [], isEmpty);
    });

    test('off = nothing is written; turning it on starts a fresh session', () async {
      final r = recorder(enabled: false);
      r.begin(HistoryMode.speak, 'zh');
      r.add(src: 'secret', tr: '祕密');
      await r.end();
      expect(await store.list(), isEmpty);
      r.setEnabled(true);
      r.add(src: 'kept', tr: '留下');
      await r.end();
      final all = await store.list();
      expect(all, hasLength(1));
      expect(all.single.lines.single.src, 'kept');
      r.setEnabled(false);
      expect(r.activeId, isNull);
    });

    test('a mode change splits the session; the same mode does not', () async {
      final r = recorder();
      r.setMode(HistoryMode.speak, 'zh');
      r.add(src: 'a', tr: 'A');
      r.setMode(HistoryMode.speak, 'en'); // same mode: same session
      r.add(src: 'b', tr: 'B', tgt: 'en');
      r.setMode(HistoryMode.video, 'zh');
      r.add(src: 'c', tr: 'C');
      await r.end();
      final all = await store.list(); // newest first
      expect(all.map((s) => s.mode), [HistoryMode.video, HistoryMode.speak]);
      expect(all[1].lines.map((l) => l.src), ['a', 'b']);
      expect(all[0].lines.map((l) => l.src), ['c']);
    });

    test('delete one, delete the running one, delete all', () async {
      final r = recorder();
      r.begin(HistoryMode.speak, 'zh');
      r.add(src: 'x', tr: 'X');
      final first = r.activeId!;
      r.begin(HistoryMode.video, 'zh');
      r.add(src: 'y', tr: 'Y');
      final second = r.activeId!;
      await r.delete(first);
      expect((await store.list()).map((s) => s.id), [second]);
      await r.delete(second); // the running session: the next line starts a new one
      expect(await store.list(), isEmpty);
      r.add(src: 'z', tr: 'Z');
      final third = r.activeId!;
      expect(third, isNot(second));
      expect(await r.deleteAll(), greaterThanOrEqualTo(0));
      expect(await store.list(), isEmpty);
      r.add(src: 'after', tr: '之後');
      await r.end();
      expect((await store.list()).single.lines.single.src, 'after');
    });

    test('prune removes sessions older than 30 days and keeps the rest', () async {
      final now = DateTime(2026, 11, 16, 12);
      Future<void> put(DateTime start) async {
        final ms = start.millisecondsSinceEpoch;
        Directory(store.dir).createSync(recursive: true);
        File('${store.dir}/$ms.jsonl').writeAsStringSync(
            HistoryFormat.header(ms, HistoryMode.speak, 'zh') +
                HistoryFormat.line(HistoryLine(atMs: ms + 1, src: 'a', tr: 'A')));
      }

      await put(now.subtract(const Duration(days: 31)));
      await put(now.subtract(const Duration(days: 29, hours: 23)));
      await put(now.subtract(const Duration(days: 1)));
      File('${store.dir}/notes.txt').writeAsStringSync('not ours');
      expect(await store.prune(now), 1);
      expect(await store.list(), hasLength(2));
      expect(File('${store.dir}/notes.txt').existsSync(), isTrue);
      expect(await store.prune(now), 0);
    });

    test('two sessions that start in the same millisecond get different files', () {
      final a = store.open(5, HistoryMode.speak, 'zh');
      final b = store.open(5, HistoryMode.speak, 'zh');
      expect(a.id, isNot(b.id));
    });
  });

  group('prefs', () {
    test('saveHistory is on by default, survives a round trip, old files keep it on', () {
      expect(const FeaturePrefs().saveHistory, isTrue);
      expect(FeaturePrefs.parse(null).saveHistory, isTrue);
      expect(FeaturePrefs.parse('sourceDraft=0\n').saveHistory, isTrue);
      final off = const FeaturePrefs().copyWith(saveHistory: false);
      expect(FeaturePrefs.parse(off.format()).saveHistory, isFalse);
      expect(FeaturePrefs.parse(off.format()), off);
    });

    test('Pipeline.setSaveHistory stops and restarts the recorder', () {
      final p = Pipeline();
      final r = recorder();
      p.history = r;
      p.setSaveHistory(false);
      expect(p.prefs.saveHistory, isFalse);
      expect(r.enabled, isFalse);
      p.setSaveHistory(true);
      expect(r.enabled, isTrue);
    });
  });

  group('screens', () {
    Future<Pipeline> seeded(WidgetTester t) async {
      final p = Pipeline();
      p.history = recorder();
      await t.runAsync(() async {
        p.history!.begin(HistoryMode.conversation, 'zh');
        p.history!.add(src: 'Where is the station?', tr: '車站在哪裡？', lang: 'en', tgt: 'zh', side: 1);
        p.history!.add(src: '在那邊', tr: "It's over there", lang: 'zh', tgt: 'en', side: 0);
        await p.history!.end();
        p.history!.begin(HistoryMode.speak, 'zh');
        p.history!.add(src: 'Привет', tr: '你好', lang: 'ru', tgt: 'zh');
        await p.history!.end();
      });
      return p;
    }

    testWidgets('list shows date, mode, languages and the first line; detail shows the bubbles',
        (t) async {
      final p = await seeded(t);
      await t.pumpWidget(MaterialApp(home: HistoryScreen(pipeline: p)));
      await settle(t);
      expect(find.textContaining('說話翻譯'), findsOneWidget);
      expect(find.textContaining('對話翻譯'), findsOneWidget);
      expect(find.textContaining('俄語 → 中文'), findsOneWidget);
      expect(find.textContaining('中文 ⇄ 英語'), findsOneWidget);
      expect(find.textContaining('車站在哪裡？'), findsOneWidget);
      expect(find.byKey(const Key('historyNote')), findsOneWidget);

      await t.tap(find.textContaining('對話翻譯'));
      await t.pumpAndSettle();
      // Both lines as bubbles, the same ones the live view uses.
      expect(find.text('車站在哪裡？'), findsOneWidget);
      expect(find.text("It's over there"), findsOneWidget);
      expect(find.byKey(const Key('deleteOne')), findsOneWidget);
    });

    testWidgets('delete all asks first; cancel keeps everything, confirm removes it', (t) async {
      final p = await seeded(t);
      await t.pumpWidget(MaterialApp(home: HistoryScreen(pipeline: p)));
      await settle(t);

      await t.tap(find.byKey(const Key('deleteAll')));
      await t.pumpAndSettle();
      await t.tap(find.text('取消'));
      await t.pumpAndSettle();
      expect((await t.runAsync(store.list))!, hasLength(2));

      await t.tap(find.byKey(const Key('deleteAll')));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('confirmDelete')));
      await settle(t);
      expect(find.byKey(const Key('historyEmpty')), findsOneWidget);
      expect((await t.runAsync(store.list))!, isEmpty);
    });

    testWidgets('long-press deletes one session after a confirm', (t) async {
      final p = await seeded(t);
      await t.pumpWidget(MaterialApp(home: HistoryScreen(pipeline: p)));
      await settle(t);
      await t.longPress(find.textContaining('說話翻譯'));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('confirmDelete')));
      await settle(t);
      expect(find.textContaining('說話翻譯'), findsNothing);
      expect(find.textContaining('對話翻譯'), findsOneWidget);
      expect((await t.runAsync(store.list))!, hasLength(1));
    });

    testWidgets('swipe deletes one session after a confirm', (t) async {
      final p = await seeded(t);
      await t.pumpWidget(MaterialApp(home: HistoryScreen(pipeline: p)));
      await settle(t);
      await t.drag(find.textContaining('說話翻譯'), const Offset(-600, 0));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('confirmDelete')));
      await settle(t);
      expect(find.textContaining('說話翻譯'), findsNothing);
      expect((await t.runAsync(store.list))!, hasLength(1));
    });

    testWidgets('the note says when saving is off', (t) async {
      final p = await seeded(t);
      p.setSaveHistory(false);
      await t.pumpWidget(MaterialApp(home: HistoryScreen(pipeline: p)));
      await t.pump();
      expect(find.textContaining('已關閉'), findsOneWidget);
    });
  });
}
