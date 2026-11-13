import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/context_budget.dart';
import 'package:mobile/logic/passthrough.dart';
import 'package:mobile/logic/prompt.dart';
import 'package:mobile/logic/s2tw.dart';
import 'package:mobile/logic/segmenter.dart';
import 'package:mobile/logic/subtitle_trim.dart';
import 'package:mobile/logic/zh_check.dart';
import 'package:mobile/services/model_paths.dart';
import 'package:mobile/services/translator.dart';

void main() {
  group('pipeline translate call (#581)', () {
    test('translateLine forwards the detected language as src', () async {
      final fake = _FakeTranslator();
      await translateLine(fake, 'こんにちは', 'ja');
      expect(fake.seenSrc, 'ja');
    });
  });
  group('zh passthrough (#581)', () {
    test('zh skips the LLM and is converted to zh-Hant', () async {
      var calls = 0;
      final out = await routeTranslate('软件开发', 'zh', (t, s) async {
        calls++;
        return 'llm';
      });
      expect(calls, 0);
      expect(out, toTraditionalTw('软件开发'));
    });
    test('non-zh reaches the generator with the detected src', () async {
      String? seen;
      final out = await routeTranslate('こんにちは', 'ja', (t, s) async {
        seen = s;
        return 'ok';
      });
      expect(seen, 'ja');
      expect(out, 'ok');
      expect(zhPassthrough('hi', 'yue'), isNull);
    });
  });
  senseVoiceRoutingTests();
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

  group('PromptBuilder (Hy-MT2, same as desktop tinglongwu/hymt.py)', () {
    // Expected strings copied from the Python output:
    //   build_prompt('Привет, как дела?', 'ru', 'zh')            (user message)
    //   that message rendered with the GGUF tokenizer.chat_template of
    //   Hy-MT2-1.8B-Q4_K_M.gguf, add_generation_prompt=True      (full prompt)
    const ru = 'Привет, как дела?';
    const desktopUser =
        '将以下文本翻译成中文，注意只需要输出翻译后的结果，不要额外解释：\n\n'
        'Привет, как дела?';
    const desktopFull = '<\uff5chy_begin\u2581of\u2581sentence\uff5c>'
        '<\uff5chy_User\uff5c>'
        '\u5c06\u4ee5\u4e0b\u6587\u672c\u7ffb\u8bd1\u6210\u4e2d\u6587\uff0c'
        '\u6ce8\u610f\u53ea\u9700\u8981\u8f93\u51fa\u7ffb\u8bd1\u540e\u7684'
        '\u7ed3\u679c\uff0c\u4e0d\u8981\u989d\u5916\u89e3\u91ca\uff1a\n\n'
        '\u041f\u0440\u0438\u0432\u0435\u0442, \u043a\u0430\u043a '
        '\u0434\u0435\u043b\u0430?'
        '<\uff5chy_Assistant\uff5c>';

    test('user message equals desktop build_prompt for ru -> zh', () {
      expect(PromptBuilder.userMessage(ru), desktopUser);
      expect(PromptBuilder.userMessage(ru, src: 'ru', tgt: 'zh'), desktopUser);
    });

    test('full prompt equals the desktop chat-template rendering', () {
      expect(PromptBuilder.build(ru), desktopFull);
    });

    test('non-Chinese pair uses the English card template', () {
      expect(
        PromptBuilder.userMessage(ru, src: 'ru', tgt: 'en'),
        'Translate the following text into English.\n'
        'Note that you should only output the translated result without any '
        'additional explanation:\n\n$ru',
      );
    });

    test('maxTokensFor matches desktop max_tokens_for', () {
      expect(PromptBuilder.maxTokensFor(''), 32);
      expect(PromptBuilder.maxTokensFor('a' * 20), 56);
      expect(PromptBuilder.maxTokensFor('a' * 500), 400);
    });

    test('clean strips template tokens, labels, quotes, extra lines', () {
      expect(PromptBuilder.clean('你好<\uff5chy_place\u2581holder\u2581no\u25812\uff5c>'),
          '你好');
      expect(PromptBuilder.clean('翻譯：你好嗎'), '你好嗎');
      expect(PromptBuilder.clean('译文：你好吗'), '你好吗');
      expect(PromptBuilder.clean('「你好」'), '你好');
      expect(PromptBuilder.clean('"你好"'), '你好');
      expect(PromptBuilder.clean('第一行\n第二行'), '第一行');
      expect(PromptBuilder.clean('   '), '');
    });
  });

  group('ModelPaths', () {
    test('translator is the Hy-MT2-1.8B GGUF in the models dir', () {
      final p = ModelPaths('/data/x');
      expect(ModelPaths.llmFile, 'Hy-MT2-1.8B-Q4_K_M.gguf');
      expect(p.llmPath, '/data/x/models/Hy-MT2-1.8B-Q4_K_M.gguf');
      expect(p.missing(), contains('/data/x/models/Hy-MT2-1.8B-Q4_K_M.gguf'));
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

  group('trimFinished (#533)', () {
    // (text, translated?)
    bool done((String, bool) s) => s.$2;

    test('drops the oldest translated lines down to keep', () {
      final l = [('a', true), ('b', true), ('c', true), ('d', false), ('e', false)];
      trimFinished(l, 4, done);
      expect(l.map((s) => s.$1), ['b', 'c', 'd', 'e']);
    });

    test('never drops lines still waiting for a translation', () {
      final l = [for (final t in ['a', 'b', 'c', 'd', 'e', 'f']) (t, false)];
      trimFinished(l, 4, done);
      expect(l.length, 6);
      l[0] = ('a', true);
      l[1] = ('b', true);
      trimFinished(l, 4, done);
      expect(l.map((s) => s.$1), ['c', 'd', 'e', 'f']);
    });

    test('skips pending lines and drops a newer translated one', () {
      final l = [('a', false), ('b', true), ('c', false), ('d', false), ('e', false)];
      trimFinished(l, 4, done);
      expect(l.map((s) => s.$1), ['a', 'c', 'd', 'e']);
    });
  });

  group('ContextBudget (#533)', () {
    // ~170 chars: one forced ASR commit (24 Russian words).
    final long = List.filled(24, 'привет').join(' ');

    test('merges the oldest two short jobs', () {
      expect(ContextBudget.mergeIndex(['раз', 'два', 'три']), 0);
      // #625: a zh/en boundary must not block a later same-language pair.
      expect(
          ContextBudget.mergeIndex(['a', 'b', 'c', 'd'],
              langs: ['zh', 'en', 'en', 'en']),
          1);
      expect(
          ContextBudget.mergeIndex(['a', 'b', 'c'], langs: ['zh', 'en', 'zh']),
          isNull);
      expect(ContextBudget.join('раз', 'два'), 'раз два');
    });

    test('does not merge when the merged text is too long', () {
      expect(long.length, greaterThan(ContextBudget.maxMergeChars ~/ 2));
      expect(ContextBudget.mergeIndex([long, long]), isNull);
      expect(ContextBudget.mergeIndex([long, long, long]), isNull);
    });

    test('skips an oversized oldest pair and merges a later pair', () {
      expect(ContextBudget.mergeIndex([long, long, 'раз']), 1);
      final huge = 'я' * 318;
      expect(ContextBudget.mergeIndex([huge, huge, 'раз', 'два']), 2);
      expect(ContextBudget.mergeIndex([huge, 'раз', 'два']), 1);
    });

    test('merge limit is the boundary', () {
      final a = 'а' * 100;
      final fits = 'б' * (ContextBudget.maxMergeChars - 101);
      expect(ContextBudget.mergeIndex([a, fits]), 0);
      expect(ContextBudget.mergeIndex([a, '${fits}x']), isNull);
    });

    test('any allowed merge plus its output budget fits the context', () {
      for (var n = 0; n <= ContextBudget.maxMergeChars; n++) {
        final t = 'я' * n;
        expect(ContextBudget.maxTokens(t), PromptBuilder.maxTokensFor(t));
        expect(ContextBudget.promptTokens(t) + ContextBudget.maxTokens(t),
            lessThan(ContextBudget.nCtx));
      }
    });

    test('maxTokens is clamped for text longer than the merge limit', () {
      final t = 'я' * 500;
      expect(PromptBuilder.maxTokensFor(t), 400);
      expect(ContextBudget.maxTokens(t),
          ContextBudget.nCtx - 1 - ContextBudget.promptTokens(t));
      expect(ContextBudget.promptTokens(t) + ContextBudget.maxTokens(t),
          lessThan(ContextBudget.nCtx));
      expect(ContextBudget.maxTokens('я' * 2000), 1);
    });
  });
}

// #563: SenseVoice language routing into the MT prompt.
void senseVoiceRoutingTests() {
  group('SenseVoice language routing (#563)', () {
    test('PromptBuilder.fromSenseVoiceLang maps <|xx|> tags, unknown -> en', () {
      expect(PromptBuilder.fromSenseVoiceLang('<|en|>'), 'en');
      expect(PromptBuilder.fromSenseVoiceLang('<|ja|>'), 'ja');
      expect(PromptBuilder.fromSenseVoiceLang('<|ko|>'), 'ko');
      expect(PromptBuilder.fromSenseVoiceLang('<|yue|>'), 'yue');
      expect(PromptBuilder.fromSenseVoiceLang('<|zh|>'), 'zh');
      expect(PromptBuilder.fromSenseVoiceLang(''), 'en');
      expect(PromptBuilder.fromSenseVoiceLang('<|nospeech|>'), 'en');
    });

    test('detected en/ja/ko prompts carry the text and tgt zh', () {
      for (final l in ['en', 'ja', 'ko', 'yue']) {
        final p = PromptBuilder.build('hello', src: l);
        expect(p, contains('将以下文本翻译成中文'));
        expect(p, endsWith('hello${PromptBuilder.assistantTag}'));
      }
    });

    test('non-Chinese target uses the source-independent English template', () {
      expect(PromptBuilder.userMessage('x', src: 'ja', tgt: 'en'), startsWith('Translate the following text into English.'));
      expect(PromptBuilder.userMessage('x', src: 'yue', tgt: 'en'), startsWith('将以下文本翻译成英语'));
    });
  });
}

class _FakeTranslator implements LineTranslator {
  String? seenSrc;
  @override
  Future<Translation> translate(String ru, {String src = 'ru'}) async {
    seenSrc = src;
    return Translation(
      zh: ru,
      check: checkChinese(ru),
      tokens: 0,
      totalMs: 0,
      firstTokenMs: 0,
      genMs: 0,
      nCtx: 0,
    );
  }
}
