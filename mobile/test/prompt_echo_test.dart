// #1615: the phone strips the card prompt's instruction when Hy-MT2 echoes it (in English, Chinese
// or translated into the target language) instead of showing it as the translation.
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/prompt.dart';

const ruEcho = 'Примите во внимание, что следует выводить только переведенный результат, '
    'без каких-либо дополнительных комментариев';
const enEcho = 'Note that you should only output the translated result without any additional explanation:';
const zhEcho = '注意只需要输出翻译后的结果，不要额外解释：';

void main() {
  group('echo only -> empty (line shown untranslated)', () {
    for (final echo in [
      ruEcho, // field test 2026-11-19, auto -> ru, source "let share。"
      '$ruEcho.',
      enEcho,
      zhEcho,
      'Обратите внимание: выводите только переведённый текст без объяснений.',
      'Bitte nur das übersetzte Ergebnis ohne zusätzliche Erklärungen ausgeben.',
      'Tenga en cuenta que solo debe mostrar el resultado traducido sin ninguna explicación adicional.',
      '翻訳結果のみを出力し、追加の説明は不要です。',
      '추가 설명 없이 번역된 결과만 출력하세요.',
      '<｜hy_Assistant｜>$ruEcho',
      enEcho.substring(0, enEcho.length - 1), // no final ":" (CodeRabbit on #1619)
      zhEcho.substring(0, zhEcho.length - 1),
    ]) {
      test(echo, () => expect(PromptBuilder.clean(echo), ''));
    }
  });

  group('echo + translation -> the translation', () {
    test('ru echo then ":" translation', () {
      expect(PromptBuilder.clean('$ruEcho: Давайте поделимся.'), 'Давайте поделимся.');
    });
    test('ru echo line then translation line', () {
      expect(PromptBuilder.clean('$ruEcho.\n\nДавайте поделимся.'), 'Давайте поделимся.');
    });
    test('en echo then translation', () {
      expect(PromptBuilder.clean('$enEcho\n\nLet us share.'), 'Let us share.');
    });
    test('zh echo then translation', () {
      expect(PromptBuilder.clean('$zhEcho\n\n我們分享吧。'), '我們分享吧。');
    });
  });

  group('real translations are unchanged', () {
    for (final t in [
      'Здравствуйте.',
      'Давайте поделимся только результатом, без лишних слов.', // no translat- part
      'Переводчик ждёт результат без комментариев. Только завтра.', // parts split across sentences
      'Je ne parle que français, sans traduction.',
      'Note that the dam was built in 1950.',
      'この翻訳だけで十分です。',
      '번역만 하면 됩니다.',
      '注意安全，不要靠近大壩。',
      'Только переводчик получил результат без комментариев.', // a translator, not the instruction
    ]) {
      test(t, () => expect(PromptBuilder.clean(t), t));
    }
  });

  group('streaming stop (lineDone)', () {
    test('an echo line alone does not stop generation', () {
      expect(PromptBuilder.lineDone('$ruEcho.\n'), isFalse);
      expect(PromptBuilder.lineDone('$enEcho\n\n'), isFalse);
      expect(PromptBuilder.lineDone('${enEcho.substring(0, enEcho.length - 1)}\n'), isFalse);
    });
    test('a translation line after the echo stops it', () {
      expect(PromptBuilder.lineDone('$ruEcho.\nДавайте поделимся.\n'), isTrue);
    });
    test('a normal first line stops it, an unfinished one does not', () {
      expect(PromptBuilder.lineDone('Здравствуйте.\n'), isTrue);
      expect(PromptBuilder.lineDone('Здравствуйте.'), isFalse);
      expect(PromptBuilder.lineDone('\n\n'), isFalse);
    });
  });
}
