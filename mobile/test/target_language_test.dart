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
}
