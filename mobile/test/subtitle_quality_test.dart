import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/prompt.dart';
import 'package:mobile/logic/segmenter.dart';
import 'package:mobile/logic/utf8_stream.dart';
import 'package:mobile/logic/zh_quotes.dart';
import 'package:mobile/services/asr_worker.dart';

void main() {
  group('Utf8Stream (split multi-byte pieces)', () {
    test('a CJK char split over three pieces decodes whole', () {
      final b = utf8.encode('我');
      final d = Utf8Stream();
      expect(d.add([b[0]]), '');
      expect(d.add([b[1]]), '');
      expect(d.add([b[2]]), '我');
      expect(d.close(), '');
    });
    test('mixed ASCII and split chars keep order, no U+FFFD', () {
      final all = utf8.encode('因為我們是 OK');
      final d = Utf8Stream();
      final sb = StringBuffer();
      for (final x in all) {
        sb.write(d.add([x]));
      }
      sb.write(d.close());
      expect(sb.toString(), '因為我們是 OK');
    });
    test('dangling partial tail is damage and gets sanitized', () {
      final d = Utf8Stream();
      final out = d.add([...utf8.encode('好'), 0xE6, 0x88]) + d.close();
      expect(out.contains('\uFFFD'), isTrue);
      expect(sanitizeOutput(out), '好');
    });
    test('sanitizeOutput drops IPA junk', () {
      expect(sanitizeOutput('因為ɪ是'), '因為是');
    });
  });
  group('zh-TW quotes', () {
    test('curly to corner brackets', () {
      expect(toTwQuotes('他說“想大一點”，又說‘好’'), '他說「想大一點」，又說『好』');
    });
    test('apostrophe without open quote is kept', () {
      expect(toTwQuotes('don’t'), 'don’t');
    });
  });
  group('faithful budget and segmentation', () {
    test('Latin source to zh gets a tight cap', () {
      final t = 'a' * 100;
      expect(PromptBuilder.faithfulMaxTokens(t), 82);
      expect(PromptBuilder.faithfulMaxTokens(t), lessThan(PromptBuilder.maxTokensFor(t)));
      expect(PromptBuilder.faithfulMaxTokens('你好', src: 'zh', tgt: 'en'),
          PromptBuilder.maxTokensFor('你好'));
    });
    test('splits at CJK sentence enders', () {
      expect(SentenceSegmenter.split('今天很好。明天呢？好！'), ['今天很好。', '明天呢？', '好！']);
    });
    test('VAD cap is short', () {
      expect(AsrConfig.vadMaxSpeech, lessThanOrEqualTo(6.0));
    });
    test('VAD keeps short real answers (#1479 replay)', () {
      // "Да." / "Нет." last 0.3-0.5 s; a longer min speech drops them before ASR.
      expect(AsrConfig.vadMinSpeech, lessThanOrEqualTo(0.3));
      expect(AsrConfig.vadThreshold, inInclusiveRange(0.3, 0.6));
    });
  });
}
