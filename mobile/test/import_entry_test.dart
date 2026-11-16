import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/logic/speech_out.dart';
import 'package:mobile/main.dart';
import 'package:mobile/services/pipeline.dart';
import 'package:mobile/ui/import_screen.dart';
import 'package:mobile/ui/subtitle_screen.dart';

const _a = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

ModelManifest _manifest() => ModelManifest.parse('''
{
  "bundle": {"name": "base-bundle.zip", "url": "https://example.invalid/base-bundle.zip"},
  "files": [
    {"path": "sensevoice/tokens.txt", "size": 10, "sha256": "$_a", "group": "asr"},
    {"path": "small.gguf", "size": 30, "sha256": "$_a", "group": "llm"}
  ]
}''', packsJson: '''
{"packs": [{"id": "voice-en", "lang": "en", "name": "tlwu-voice-en.zip", "url": "u",
  "source": "https://example.invalid/vits.tar.bz2",
  "files": [
    {"path": "tts-en/model.onnx", "size": 5, "sha256": "$_a"},
    {"path": "tts-en/tokens.txt", "size": 6, "sha256": "$_a"},
    {"path": "tts-en/lexicon.txt", "size": 7, "sha256": "$_a"}
  ]}]}''');

void _file(String path, int size) => File(path)
  ..createSync(recursive: true)
  ..writeAsBytesSync(List.filled(size, 0));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('com.llfbandit.record/messages'), (call) async => null);

  Pipeline make({bool conversation = false, String foreign = 'en'}) {
    final p = Pipeline()..stage = Stage.listening;
    if (conversation) p.conversation = Conversation(enabled: true, foreign: foreign);
    return p;
  }

  test('the pipeline restarts after Import only when needed', () {
    // Not listening (models missing / error): always a fresh start.
    expect(restartAfterImport(listening: false, imported: false), isTrue);
    expect(restartAfterImport(listening: false, imported: null), isTrue);
    // Listening: only when something was imported (no model reload for just looking).
    expect(restartAfterImport(listening: true, imported: true), isTrue);
    expect(restartAfterImport(listening: true, imported: false), isFalse);
    expect(restartAfterImport(listening: true, imported: null), isFalse);
  });

  testWidgets('the settings sheet opens Import models', (t) async {
    var opened = 0;
    await t.pumpWidget(
        MaterialApp(home: SubtitleScreen(pipeline: make(), onImport: () => opened++)));
    await t.tap(find.byTooltip('字幕顯示設定'));
    await t.pumpAndSettle();
    final entry = find.byKey(const Key('importEntry'));
    await t.ensureVisible(entry);
    await t.tap(entry);
    await t.pumpAndSettle();
    expect(opened, 1);
    expect(entry, findsNothing); // the sheet closed
  });

  testWidgets('no Import entry without a callback', (t) async {
    await t.pumpWidget(MaterialApp(home: SubtitleScreen(pipeline: make())));
    await t.tap(find.byTooltip('字幕顯示設定'));
    await t.pumpAndSettle();
    expect(find.byKey(const Key('importEntry')), findsNothing);
  });

  test('packMissing: only when a pack for the other language can be installed', () {
    final packs = _manifest().packs; // an English voice only
    final en = make(conversation: true)..packs = packs;
    expect(en.packMissing, isTrue); // no English voice installed
    en.ttsInstalled = true;
    expect(en.packMissing, isFalse);

    final ja = make(conversation: true, foreign: 'ja')..packs = packs;
    expect(ja.voiceAvailable, isFalse);
    expect(ja.packMissing, isFalse);
    expect(voiceStatusLabel(installed: false, speaking: false, available: false),
        isNot(contains('未安裝')));
    // The recognizer failed to load: that is still something to (re)install.
    final ru = make(conversation: true, foreign: 'ru')
      ..packs = const []
      ..extraAsrMissing = true;
    expect(ru.packMissing, isTrue);
    // Manifest not read: keep the old wording (the voice is "not installed").
    expect(make(conversation: true).packMissing, isTrue);
  });

  group('Import screen', () {
    late Directory dir;
    setUp(() => dir = Directory.systemTemp.createTempSync('tlwu_import'));
    tearDown(() => dir.deleteSync(recursive: true));

    Future<bool?> open(WidgetTester t) async {
      bool? result;
      var popped = false;
      await t.pumpWidget(MaterialApp(
          home: Builder(
              builder: (c) => TextButton(
                  onPressed: () async {
                    result = await Navigator.of(c).push<bool>(MaterialPageRoute(
                        builder: (_) => ImportScreen(modelsDir: dir.path, manifest: _manifest())));
                    popped = true;
                  },
                  child: const Text('open')))));
      await t.tap(find.text('open'));
      await t.pumpAndSettle();
      expect(find.text('匯入模型 / Import models'), findsOneWidget);
      addTearDown(() => expect(popped, isTrue));
      _result = () => result;
      return null;
    }

    testWidgets('base models present: only the optional packs are offered', (t) async {
      _file('${dir.path}/sensevoice/tokens.txt', 10);
      _file('${dir.path}/small.gguf', 30);
      await open(t);
      expect(find.byKey(const Key('optionalIntro')), findsOneWidget);
      expect(find.textContaining('base-bundle.zip'), findsNothing);
      expect(find.textContaining('tlwu-voice-en.zip'), findsWidgets);
      expect(find.textContaining('已安裝'), findsNWidgets(1)); // only the intro line
      // System back reports "nothing imported", like the back arrow.
      await t.binding.handlePopRoute();
      await t.pumpAndSettle();
      expect(_result(), isFalse);
    });

    testWidgets('base models missing: the bundle is offered; installed pack is marked', (t) async {
      _file('${dir.path}/tts-en/model.onnx', 5);
      _file('${dir.path}/tts-en/tokens.txt', 6);
      _file('${dir.path}/tts-en/lexicon.txt', 7);
      await open(t);
      expect(find.byKey(const Key('optionalIntro')), findsNothing);
      expect(find.textContaining('base-bundle.zip'), findsWidgets);
      expect(find.textContaining('✓ 已安裝。選用：對話模式朗讀語音'), findsOneWidget);
      await t.tap(find.byType(BackButton));
      await t.pumpAndSettle();
      expect(_result(), isFalse);
    });
  });
}

bool? Function() _result = () => null;
