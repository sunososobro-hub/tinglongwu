import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/passthrough.dart';
import 'package:mobile/logic/prompt.dart';
import 'package:mobile/logic/target_language.dart';

void main() {
  group('subtitle target language (#730)', () {
    test('target en, src ja: prompt asks for English', () {
      final m = PromptBuilder.userMessage('こんにちは', src: 'ja', tgt: 'en');
      expect(m, contains('into English'));
      expect(m, isNot(contains('中文')));
    });

    test('src == target passes text through unchanged (en)', () async {
      var calls = 0;
      final out = await routeTranslate(' hello ', 'en', (t, s) async {
        calls++;
        return 'llm';
      }, tgt: 'en');
      expect(calls, 0);
      expect(out, 'hello');
    });

    test('target en, src zh still reaches the translator', () async {
      String? seen;
      final out = await routeTranslate('你好', 'zh', (t, s) async {
        seen = s;
        return 'hello';
      }, tgt: 'en');
      expect(seen, 'zh');
      expect(out, 'hello');
    });

    test('default target zh keeps the zh passthrough', () {
      expect(zhPassthrough('hi', 'en'), isNull);
      expect(passthrough('hi', 'zh'), isNotNull);
    });

    test('selector reads as a target, source is auto-detected', () {
      expect(TargetLanguage.targetLabel('zh'), '翻成：繁體中文');
      expect(TargetLanguage.targetLabel('en'), '翻成：English');
      expect(TargetLanguage.targetLabel('xx'), '翻成：繁體中文');
      expect(TargetLanguage.sourceHint, '自動辨識說話語言');
    });

    test('picker value survives a restart (settings file)', () {
      final dir = Directory.systemTemp.createTempSync('tlwu730');
      addTearDown(() => dir.deleteSync(recursive: true));
      final path = '${dir.path}/target_language.txt';
      expect(TargetLanguageStore(path).load(), 'zh'); // nothing saved yet
      TargetLanguageStore(path).save('en');
      expect(TargetLanguageStore(path).load(), 'en'); // new instance = restart
      File(path).writeAsStringSync('garbage');
      expect(TargetLanguageStore(path).load(), 'zh');
    });
  });

  test('menu offers ja/ko/es/fr/de/th/vi/id in their own language; non-zh prompts use English card', () {
    expect(TargetLanguage.options['ja'], '日本語');
    expect(TargetLanguage.options['ko'], '한국어');
    for (final c in ['ja', 'ko', 'es', 'fr', 'de', 'th', 'vi', 'id']) {
      expect(TargetLanguage.normalize(c), c);
      expect(PromptBuilder.userMessage('hello', src: 'en', tgt: c), contains(PromptBuilder.enNames[c]!));
    }
    expect(passthrough(' こんにちは ', 'ja', tgt: 'ja'), 'こんにちは');
  });

  test('Russian is a subtitle language (shown as Русский); the prompts name it', () {
    expect(TargetLanguage.options['ru'], 'Русский');
    expect(TargetLanguage.normalize('ru'), 'ru');
    expect(TargetLanguage.normalize('RU '), 'ru');
    expect(PromptBuilder.userMessage('hello', src: 'en', tgt: 'ru'), contains('into Russian'));
    expect(PromptBuilder.userMessage('你好', src: 'zh', tgt: 'ru'), contains('俄语'));
    expect(TargetLanguage.targetLabel('ru'), '翻成：Русский');
  });

  test('target ru with a ru line passes the text through untouched (no zh conversion)', () {
    expect(passthrough(' Привет ', 'ru', tgt: 'ru'), 'Привет');
  });
}
