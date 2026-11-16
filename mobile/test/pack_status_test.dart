import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/logic/pack_status.dart';
import 'package:mobile/services/pipeline.dart';
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

/// The shipped set: an English voice, a Russian recognizer and a Russian voice.
final _packs = [
  _pack('voice-en', 'en', 112 * _mb),
  _pack('lang-ru', 'ru', 70 * _mb, kind: 'asr'),
  _pack('voice-ru', 'ru', 133 * _mb, kind: 'supertonic'),
];

LangPackStatus _st(String lang, {Set<String> voices = const {}, Set<String> asr = const {}}) =>
    LangPackStatus.of(lang, packs: _packs, hasVoice: voices.contains, hasAsr: asr.contains);

void main() {
  group('LangPackStatus', () {
    test('English: heard by the built-in recognizer, voice to download', () {
      final s = _st('en');
      expect(s.hearing, Hearing.builtIn);
      expect(s.voice, Voice.packMissing);
      expect(s.mark, PackMark.download);
      expect(s.downloadLabel, '下載語音包 (112 MB)');
      expect(s.toDownload.map((p) => p.id), ['voice-en']);
      expect(s.voiceLine, contains('112 MB'));
    });

    test('installed voice: nothing to download', () {
      final s = _st('en', voices: {'en'});
      expect(s.voice, Voice.installed);
      expect(s.mark, PackMark.voice);
      expect(s.downloadLabel, '');
      expect(s.toDownload, isEmpty);
    });

    test('Russian needs the recognizer and the voice, recognizer first', () {
      final none = _st('ru');
      expect(none.hearing, Hearing.packMissing);
      expect(none.hearingOk, isFalse);
      expect(none.toDownload.map((p) => p.id), ['lang-ru', 'voice-ru']);
      expect(none.downloadLabel, '下載辨識包和語音包 (203 MB)');
      expect(none.hearingLine, contains('Русский辨識包（70 MB）'));

      expect(_st('ru', asr: {'ru'}).downloadLabel, '下載語音包 (133 MB)');
      expect(_st('ru', voices: {'ru'}).downloadLabel, '下載辨識包 (70 MB)');
      final all = _st('ru', voices: {'ru'}, asr: {'ru'});
      expect(all.hearing, Hearing.packInstalled);
      expect(all.mark, PackMark.voice);
      expect(all.downloadLabel, '');
    });

    test('a language without a voice is text only, with nothing to download', () {
      final ja = _st('ja');
      expect(ja.hearing, Hearing.builtIn);
      expect(ja.voice, Voice.none);
      expect(ja.mark, PackMark.textOnly);
      expect(ja.downloadLabel, '');
      expect(ja.voiceLine, contains('還沒有語音'));
    });

    test('a language the phone cannot hear says so', () {
      final es = _st('es');
      expect(es.hearing, Hearing.cannot);
      expect(es.hearingOk, isFalse);
      expect(es.hearingLine, contains('還不行'));
      expect(es.hearingLine, contains('Español'));
      expect(es.downloadLabel, '');
    });

    test('every 對方語言 has a status from the shipped manifest', () {
      final m = ModelManifest.parse(File('assets/models_manifest.json').readAsStringSync(),
          packsJson: File('assets/voice_packs.json').readAsStringSync());
      for (final lang in Conversation.foreignOptions.keys) {
        final s = LangPackStatus.of(lang,
            packs: m.packs, hasVoice: (_) => false, hasAsr: (_) => false);
        expect(s.hearingLine, isNotEmpty);
        expect(s.voiceLine, isNotEmpty);
      }
      String label(String l) =>
          LangPackStatus.of(l, packs: m.packs, hasVoice: (_) => false, hasAsr: (_) => false)
              .downloadLabel;
      expect(label('en'), matches(RegExp(r'^下載語音包 \(\d+ MB\)$')));
      expect(label('ru'), matches(RegExp(r'^下載辨識包和語音包 \(\d+ MB\)$')));
    });

    test('packSize', () {
      expect(packSize(112 * _mb), '112 MB');
      expect(packSize(1500000000), '1.50 GB');
      expect(packSize(117833721), '118 MB'); // the shipped English voice
    });
  });

  group('conversation sheet', () {
    TestWidgetsFlutterBinding.ensureInitialized();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('com.llfbandit.record/messages'), (call) async => null);

    Pipeline make({String foreign = 'en'}) => Pipeline()
      ..stage = Stage.listening
      ..packs = _packs
      ..conversation = Conversation(enabled: true, foreign: foreign);

    Future<void> show(WidgetTester t, Pipeline p, {VoidCallback? onImport}) async {
      await t.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => t.binding.setSurfaceSize(null));
      await t.pumpWidget(MaterialApp(
          home: ListenableBuilder(
              listenable: p,
              builder: (_, _) => SubtitleScreen(pipeline: p, onImport: onImport))));
    }

    Future<void> tapKey(WidgetTester t, String key) async {
      final f = find.byKey(Key(key));
      await t.ensureVisible(f);
      await t.pumpAndSettle();
      await t.tap(f);
      await t.pumpAndSettle();
    }

    testWidgets('a missing pack has a tappable action on the screen', (t) async {
      var installs = 0;
      final p = make();
      await show(t, p, onImport: () => installs++);
      expect(find.text('下載語音包 (112 MB)'), findsOneWidget);
      await tapKey(t, 'packAction');
      expect(installs, 1);
    });

    testWidgets('no action for a language without packs, or when installed', (t) async {
      final p = make(foreign: 'ja');
      await show(t, p, onImport: () {});
      expect(find.byKey(const Key('packAction')), findsNothing);
      expect(find.textContaining('這個語言還沒有語音'), findsOneWidget); // the pill says why

      p.setPartnerLang('en');
      p.ttsInstalled = true;
      p.notifyListeners();
      await t.pumpAndSettle();
      expect(find.byKey(const Key('packAction')), findsNothing);
    });

    testWidgets('the pill opens two separate pickers and the speaker toggle', (t) async {
      var installs = 0;
      final p = make();
      await show(t, p, onImport: () => installs++);
      await tapKey(t, 'pairPill');
      expect(find.text('我的語言'), findsOneWidget);
      expect(find.text('對方語言'), findsOneWidget);
      expect(find.text('誰在說話'), findsOneWidget);
      // Every language is a chip in its own section; my language is not offered as 對方語言.
      expect(find.byKey(const Key('mine:zh')), findsOneWidget);
      expect(find.byKey(const Key('partner:zh')), findsNothing);
      for (final code in Conversation.foreignOptions.keys.where((c) => c != 'zh')) {
        expect(find.byKey(Key('partner:$code')), findsOneWidget);
      }
      // English: built in, voice to download.
      expect(find.text('聽懂對方：可以（內建）'), findsOneWidget);
      expect(find.text('朗讀給對方聽：需要下載語音包（112 MB）'), findsOneWidget);

      await tapKey(t, 'partner:ru');
      expect(p.conversation.foreign, 'ru');
      expect(find.text('聽懂對方：需要下載Русский辨識包（70 MB）'), findsOneWidget);
      expect(find.text('下載辨識包和語音包 (203 MB)'), findsWidgets);

      await tapKey(t, 'partner:es');
      expect(find.textContaining('聽懂對方：還不行'), findsOneWidget);
      expect(find.byKey(const Key('sheetDownload')), findsNothing);

      await tapKey(t, 'mine:en');
      expect(p.conversation.mineLang, 'en');
      expect(find.byKey(const Key('partner:en')), findsNothing);
      expect(find.byKey(const Key('partner:zh')), findsOneWidget);

      expect(p.conversation.auto, isTrue);
      await t.ensureVisible(find.text('手動切換'));
      await t.tap(find.text('手動切換'));
      await t.pumpAndSettle();
      expect(p.conversation.auto, isFalse);

      await tapKey(t, 'partner:ru');
      await tapKey(t, 'sheetDownload');
      expect(installs, 1);
      expect(find.text('誰在說話'), findsNothing); // the sheet closed
      expect(find.byKey(const Key('sideButton')), findsOneWidget); // manual mode
    });
  });
}
