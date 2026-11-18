import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/level_gate.dart';

void main() {
  test('rmsDb of a constant signal', () {
    expect(LevelGate.rmsDb(List.filled(1600, 0.1)), closeTo(-20.0, 0.01));
    expect(LevelGate.rmsDb(const []), -120);
    expect(LevelGate.rmsDb(List.filled(10, 0.0)), -120);
  });

  test('quiet short background words are dropped, loud short answers kept', () {
    final g = LevelGate();
    // foreground sentence (-21 dBFS, 3 s) sets the reference
    expect(g.shouldDrop(levelDb: -21, durSec: 3, nowSec: 1), isFalse);
    // background filler (-38 dBFS, 0.4 s): under the floor and 17 dB under the reference
    expect(g.shouldDrop(levelDb: -38, durSec: 0.4, nowSec: 5), isTrue);
    // a close short answer ("Да.", -22 dBFS) survives
    expect(g.shouldDrop(levelDb: -22, durSec: 0.5, nowSec: 8), isFalse);
  });

  test('absolute floor works with no reference (background-only room)', () {
    final g = LevelGate();
    expect(g.shouldDrop(levelDb: -38, durSec: 0.5, nowSec: 1), isTrue);
    expect(g.shouldDrop(levelDb: -25, durSec: 0.5, nowSec: 3), isFalse);
  });

  test('relative gate: short segment far below a loud reference is dropped', () {
    final g = LevelGate();
    expect(g.shouldDrop(levelDb: -12, durSec: 2, nowSec: 1), isFalse);
    expect(g.shouldDrop(levelDb: -25, durSec: 0.6, nowSec: 4), isTrue); // 13 dB under
    expect(g.shouldDrop(levelDb: -20, durSec: 0.6, nowSec: 5), isFalse); // 8 dB under
  });

  test('long segments are never dropped, even when quiet', () {
    final g = LevelGate();
    expect(g.shouldDrop(levelDb: -45, durSec: 3, nowSec: 1), isFalse);
    expect(g.shouldDrop(levelDb: -45, durSec: LevelGate.maxShortSec, nowSec: 2), isTrue);
  });

  test('reference expires after the window', () {
    final g = LevelGate();
    expect(g.shouldDrop(levelDb: -12, durSec: 2, nowSec: 0), isFalse);
    // a dropped segment leaves the history intact: a second probe is dropped too
    expect(g.shouldDrop(levelDb: -25, durSec: 0.6, nowSec: 10), isTrue);
    expect(g.shouldDrop(levelDb: -25, durSec: 0.6, nowSec: 20), isTrue);
    // still the reference at the window edge
    expect(g.shouldDrop(levelDb: -25, durSec: 0.6, nowSec: LevelGate.refWindowSec), isTrue);
    final later = LevelGate.refWindowSec + 1;
    expect(g.shouldDrop(levelDb: -25, durSec: 0.6, nowSec: later), isFalse);
  });

  test('the loud reference survives seven later kept segments (eight kept entries)', () {
    final g = LevelGate();
    expect(g.shouldDrop(levelDb: -12, durSec: 2, nowSec: 0), isFalse);
    for (var i = 1; i <= 7; i++) {
      expect(g.shouldDrop(levelDb: -22, durSec: 2, nowSec: i.toDouble()), isFalse);
    }
    expect(g.shouldDrop(levelDb: -25, durSec: 0.6, nowSec: 9), isTrue); // 13 dB under -12
  });
}
