import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/flip_detector.dart';

Duration ms(int n) => Duration(milliseconds: n);

void main() {
  test('flips only after upside-down is held 500 ms', () {
    final d = FlipDetector();
    expect(d.update(-0.9, ms(0)), false);
    expect(d.update(-0.9, ms(400)), false);
    expect(d.update(-0.9, ms(500)), true);
  });

  test('a brief tilt does not flip', () {
    final d = FlipDetector();
    d.update(-0.9, ms(0));
    d.update(0.9, ms(200));
    expect(d.update(-0.9, ms(600)), false);
  });

  test('flat keeps state (hysteresis)', () {
    final d = FlipDetector(flipped: true);
    expect(d.update(0.0, ms(0)), true);
    expect(d.update(0.2, ms(2000)), true);
  });

  test('upright held 500 ms flips back', () {
    final d = FlipDetector(flipped: true);
    d.update(0.9, ms(0));
    expect(d.update(0.9, ms(500)), false);
  });

  test('lock freezes state', () {
    final d = FlipDetector(locked: true);
    d.update(-1.0, ms(0));
    expect(d.update(-1.0, ms(1000)), false);
  });
}
