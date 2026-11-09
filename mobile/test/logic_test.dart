import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/prompt.dart';
import 'package:mobile/logic/segmenter.dart';
import 'package:mobile/logic/zh_check.dart';

void main() {
  group('SentenceSegmenter', () {
    test('normalizes whitespace', () {
      expect(SentenceSegmenter.normalize('  привет   мир \n'), 'привет мир');
      expect(SentenceSegmenter.wordCount('  a  b c '), 3);
      expect(SentenceSegmenter.wordCount('   '), 0);
    });

    test('empty and punctuation-only input yields nothing', () {
      expect(SentenceSegmenter.split(''), isEmpty);
      expect(SentenceSegmenter.split('  ...  '), isEmpty);
    });

    test('unpunctuated short text stays one sentence', () {
      expect(SentenceSegmenter.split('привет как дела'), ['привет как дела']);
    });

    test('splits on sentence punctuation', () {
      expect(
        SentenceSegmenter.split('Привет. Как дела? Хорошо!'),
        ['Привет.', 'Как дела?', 'Хорошо!'],
      );
    });

    test('does not split decimals / inner dots without following space', () {
      expect(SentenceSegmenter.split('цена 3.5 рубля'), ['цена 3.5 рубля']);
    });

    test('ellipsis stays attached to one sentence', () {
      expect(SentenceSegmenter.split('ну... хорошо'), ['ну...', 'хорошо']);
    });

    test('long text is chunked at maxWords', () {
      final text = List.generate(50, (i) => 'w$i').join(' ');
      final parts = SentenceSegmenter.split(text, maxWords: 20);
      expect(parts.length, 3);
      expect(parts.map(SentenceSegmenter.wordCount).toList(), [20, 20, 10]);
      expect(parts.join(' '), text);
    });

    test('long text prefers comma boundary in back half', () {
      final words = List.generate(30, (i) => 'w$i');
      words[16] = 'w16,';
      final parts = SentenceSegmenter.split(words.join(' '), maxWords: 20);
      expect(parts.first.endsWith('w16,'), isTrue);
      expect(parts.join(' '), words.join(' '));
    });

    test('shouldForceCommit threshold', () {
      final t = List.filled(24, 'a').join(' ');
      expect(SentenceSegmenter.shouldForceCommit(t), isTrue);
      expect(SentenceSegmenter.shouldForceCommit('a b c'), isFalse);
      expect(SentenceSegmenter.shouldForceCommit(''), isFalse);
    });
  });

  group('PromptBuilder', () {
    test('contains no_think, think block and Traditional instruction', () {
      final p = PromptBuilder.build('привет мир');
      expect(p, startsWith('<|im_start|>system\n'));
      expect(p, contains('Traditional Chinese'));
      expect(p, contains('привет мир /no_think<|im_end|>'));
      expect(p, endsWith('<|im_start|>assistant\n<think>\n\n</think>\n\n'));
      expect(p, isNot(contains('Previous sentences')));
    });

    test('includes at most the last 2 context pairs, in order', () {
      final ctx = [
        const ContextPair('один', '一'),
        const ContextPair('два', '二'),
        const ContextPair('три', '三'),
      ];
      final p = PromptBuilder.build('четыре', context: ctx);
      expect(p, isNot(contains('один')));
      expect(p.indexOf('два') < p.indexOf('три'), isTrue);
      expect(p, contains('RU: три\nZH: 三'));
      expect(p.indexOf('три') < p.indexOf('четыре'), isTrue);
    });

    test('strict mode adds the Traditional reminder', () {
      expect(PromptBuilder.build('x', strict: true), contains('IMPORTANT'));
      expect(PromptBuilder.build('x'), isNot(contains('IMPORTANT')));
    });

    test('clean strips think blocks, labels, quotes, extra lines', () {
      expect(PromptBuilder.clean('<think>\n\n</think>\n\n你好'), '你好');
      expect(PromptBuilder.clean('翻譯：你好嗎'), '你好嗎');
      expect(PromptBuilder.clean('「你好」'), '你好');
      expect(PromptBuilder.clean('"你好"'), '你好');
      expect(PromptBuilder.clean('你好<|im_end|>'), '你好');
      expect(PromptBuilder.clean('第一行\n第二行'), '第一行');
      expect(PromptBuilder.clean('   '), '');
    });
  });

  group('zh_check', () {
    test('Traditional text passes', () {
      expect(isTraditionalChinese('這是一個測試，我們說話的時候。'), isTrue);
      expect(isTraditionalChinese('你好嗎'), isTrue);
    });

    test('Simplified text fails', () {
      final c = checkChinese('这是一个测试，我们说话的时候。');
      expect(c.isTraditional, isFalse);
      expect(c.simplifiedCount, greaterThan(0));
    });

    test('mixed text fails', () {
      expect(isTraditionalChinese('這是一个測試'), isFalse);
    });

    test('Cyrillic leakage fails', () {
      final c = checkChinese('привет 你好');
      expect(c.hasCyrillic, isTrue);
      expect(c.isTraditional, isFalse);
    });

    test('no Chinese at all fails', () {
      expect(isTraditionalChinese('hello'), isFalse);
      expect(isTraditionalChinese(''), isFalse);
    });

    test('digits and punctuation do not count as CJK', () {
      final c = checkChinese('123，。!');
      expect(c.cjkCount, 0);
    });
  });
}
