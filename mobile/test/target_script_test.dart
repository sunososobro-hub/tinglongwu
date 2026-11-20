// #1616: phone 說話翻譯, auto-detect -> Русский. The zh line "聽少說嗎?" was shown with the English
// translation "Do you want to talk or not?". The route and the prompt do ask for Russian (tests
// below), so the English answer is model drift: the wrong-script answer is retried once with the
// other card template, and dropped (shown untranslated) when it is still wrong.
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/logic/prompt.dart';
import 'package:mobile/logic/target_script.dart';
import 'package:mobile/logic/zh_check.dart';
import 'package:mobile/services/translator.dart';

const src = '聽少說嗎?';
const en = 'Do you want to talk or not?';
const ru = 'Ты хочешь поговорить или нет?';

void main() {
  group('the field-test path asks for Russian', () {
    test('talk mode (listen-only route) keeps tgt ru for a zh line', () async {
      final route = const Conversation().route('zh', listenTarget: 'ru');
      expect(route.src, 'zh');
      expect(route.tgt, 'ru');
      final fake = _RecordingTranslator();
      await translateLine(fake, src, route.src, tgt: route.tgt);
      expect(fake.seen, ['zh>ru']);
    });

    test('prompt names Russian (Chinese card), the retry template too (English card)', () {
      final p = PromptBuilder.build(src, src: 'zh', tgt: 'ru');
      expect(p, contains('将以下文本翻译成俄语'));
      expect(p, isNot(contains('英语')));
      final alt = PromptBuilder.build(src, src: 'zh', tgt: 'ru', alt: true);
      expect(alt, contains('Translate the following text into Russian.'));
      expect(alt, endsWith('$src${PromptBuilder.assistantTag}'));
    });

    test('alt flips the English card to the Chinese one', () {
      expect(PromptBuilder.userMessage('x', src: 'ja', tgt: 'en', alt: true), startsWith('将以下文本翻译成英语'));
      expect(PromptBuilder.userMessage('x', src: 'ja', tgt: 'en'), startsWith('Translate the following text into English.'));
    });
  });

  group('wrongScript', () {
    test('English answer for a Russian target', () => expect(wrongScript(src, en, 'ru'), isTrue));
    test('Russian answer is fine', () => expect(wrongScript(src, ru, 'ru'), isFalse));
    test('Russian answer with a kept Latin name is fine',
        () => expect(wrongScript('我用iPhone', 'Я пользуюсь iPhone', 'ru'), isFalse));
    test('name copied from the source is not drift',
        () => expect(wrongScript('iPhone', 'iPhone', 'ru'), isFalse));
    test('OK kept as written is not drift', () => expect(wrongScript('OK嗎', 'OK?', 'ru'), isFalse));
    test('empty answer is not wrong script (shown untranslated anyway)',
        () => expect(wrongScript(src, '', 'ru'), isFalse));
    test('digits / punctuation only are fine', () => expect(wrongScript('三點', '3:00', 'ru'), isFalse));
    test('English answer for a zh target', () => expect(wrongScript('Привет, как дела?', 'Hi, how are you?', 'zh'), isTrue));
    test('Chinese answer for a zh target', () => expect(wrongScript('Привет', '你好', 'zh'), isFalse));
    test('Chinese answer for an English target', () => expect(wrongScript('Привет', '你好', 'en'), isTrue));
    test('Japanese answer may be kanji only', () => expect(wrongScript('你好', '今日', 'ja'), isFalse));
    test('English answer for a Korean target', () => expect(wrongScript(src, en, 'ko'), isTrue));
    test('unknown target is never checked', () => expect(wrongScript(src, en, 'xx'), isFalse));
  });

  group('translateInScript', () {
    test('English first answer -> retried with the other template -> Russian', () async {
      final prompts = <String>[];
      final r = await translateInScript(src, 'ru', (alt) async {
        prompts.add(PromptBuilder.build(src, src: 'zh', tgt: 'ru', alt: alt));
        return alt ? ru : en;
      });
      expect(r.text, ru);
      expect(r.note, 'retry');
      expect(prompts, hasLength(2));
      expect(prompts[0], contains('翻译成俄语'));
      expect(prompts[1], contains('into Russian'));
    });

    test('still English after the retry -> dropped (line shown untranslated)', () async {
      final r = await translateInScript(src, 'ru', (alt) async => en);
      expect(r.text, '');
      expect(r.note, 'dropped');
    });

    test('a right first answer costs one call only', () async {
      var calls = 0;
      final r = await translateInScript(src, 'ru', (alt) async {
        calls++;
        return ru;
      });
      expect(r.text, ru);
      expect(r.note, '');
      expect(calls, 1);
    });
  });
}

class _RecordingTranslator implements LineTranslator {
  final seen = <String>[];
  @override
  Future<Translation> translate(String text, {String src = 'ru', String tgt = 'zh'}) async {
    seen.add('$src>$tgt');
    return Translation(
        zh: ru, check: checkChinese(ru), tokens: 0, totalMs: 0, firstTokenMs: 0, genMs: 0, nCtx: 0);
  }
}
