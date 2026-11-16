import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/pipeline.dart';
import 'package:mobile/ui/subtitle_screen.dart';
import 'package:mobile/logic/feature_prefs.dart';
import 'package:mobile/logic/tilt_flip.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('com.llfbandit.record/messages'), (call) async => null);
  const g = TiltFlip.gravity;
  // Feeds a constant gravity vector (in g) every 50 ms for [ms]; returns the final state.
  bool run(TiltFlipDetector d, double x, double y, double z, int ms, int t0) {
    var r = d.flipped;
    for (var t = t0; t <= t0 + ms; t += 50) {
      r = d.feed(x * g, y * g, z * g, t);
    }
    return r;
  }

  test('upright never flips', () {
    final d = TiltFlipDetector();
    expect(run(d, 0, 1, 0, 3000, 0), false);
  });

  test('upside-down flips only after the hold time', () {
    final d = TiltFlipDetector();
    run(d, 0, 1, 0, 500, 0);
    // smoothing makes it take a moment to cross, then 500 ms hold
    expect(run(d, 0, -1, 0, 200, 600), false);
    expect(run(d, 0, -1, 0, 1500, 800), true);
  });

  test('flips back with hysteresis', () {
    final d = TiltFlipDetector();
    run(d, 0, -1, 0, 2000, 0);
    expect(d.flipped, true);
    // between exitY and enterY: stays flipped
    expect(run(d, 0, -0.35, 0, 2000, 2100), true);
    expect(run(d, 0, 1, 0, 2000, 4200), false);
  });

  test('lying flat keeps the last state', () {
    final d = TiltFlipDetector();
    run(d, 0, -1, 0, 2000, 0);
    expect(run(d, 0, 0.05, 1, 3000, 2100), true);
    final e = TiltFlipDetector();
    run(e, 0, 1, 0, 1000, 0);
    expect(run(e, 0, -0.05, -1, 3000, 1100), false);
  });

  test('walking jitter does not flip', () {
    final d = TiltFlipDetector();
    var t = 0;
    for (var i = 0; i < 100; i++) {
      // brief dips below the threshold, never held for 500 ms
      final y = (i % 6 == 0) ? -1.2 : 0.9;
      d.feed(0, y * g, 0, t);
      t += 50;
    }
    expect(d.flipped, false);
  });

  test('tilt lock pref round-trips and defaults on', () {
    expect(const FeaturePrefs().tiltFlip, true);
    final p = const FeaturePrefs().copyWith(tiltFlip: false);
    expect(FeaturePrefs.parse(p.format()).tiltFlip, false);
    expect(FeaturePrefs.parse('sourceDraft=1\n').tiltFlip, true);
  });

  testWidgets('screen rotates 180 deg when flipped and lock stops it', (t) async {
    final c = StreamController<List<double>>();
    final p = Pipeline()..stage = Stage.listening;
    await t.pumpWidget(MaterialApp(home: SubtitleScreen(pipeline: p, gravity: c.stream)));
    expect(find.byKey(const Key('listenerView')), findsNothing);
    // real clock is used by the screen, so feed the upside-down pose for > holdMs
    final end = DateTime.now().add(const Duration(milliseconds: 800));
    while (DateTime.now().isBefore(end)) {
      c.add([0, -TiltFlip.gravity, 0]);
      await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    }
    await t.pump();
    expect(find.byKey(const Key('listenerView')), findsOneWidget);
    await t.tap(find.byKey(const Key('tiltLockToggle')));
    c.add([0, -TiltFlip.gravity, 0]);
    await t.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
    await t.pump();
    expect(p.prefs.tiltFlip, false);
    expect(find.byKey(const Key('listenerView')), findsNothing);
    await c.close();
  });
}
