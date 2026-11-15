import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/debug_stats.dart';

void main() {
  test('debug stats hidden in release unless forced (#1118)', () {
    expect(showDebugStats(releaseMode: true, forced: false), isFalse);
    expect(showDebugStats(releaseMode: true, forced: true), isTrue);
    expect(showDebugStats(releaseMode: false, forced: false), isTrue);
  });
}
