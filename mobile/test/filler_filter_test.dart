import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/filler_filter.dart';

void main() {
  test('filler-only short finals are dropped (ru/en/zh/ja/ko)', () {
    for (final t in ['и', 'ну вот', 'Там.', 'uh', 'um, hmm', '嗯嗯', '啊', 'えっと', '음', '', '...']) {
      expect(FillerFilter.shouldDrop(t), isTrue, reason: t);
    }
  });
  test('short real answers and longer text are kept', () {
    for (final t in ['нет', 'yes', '好', 'да нет', 'да', 'うん', '응', 'ну да ладно', 'um I think so', 'はい', '네']) {
      expect(FillerFilter.shouldDrop(t), isFalse, reason: t);
    }
  });
}
