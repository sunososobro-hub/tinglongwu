import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/draft_gate.dart';
import 'package:mobile/logic/feature_prefs.dart';

Float32List chunk(int n, [double v = 0.1]) => Float32List(n)..fillRange(0, n, v);

void main() {
  group('DraftGate throttle', () {
    test('needs minimum audio', () {
      final g = DraftGate();
      expect(g.shouldRun(nowMs: 5000, samples: 8000), isFalse);
      expect(g.shouldRun(nowMs: 5000, samples: 9600), isTrue);
    });

    test('runs at most every interval and only with new audio', () {
      final g = DraftGate();
      g.started(nowMs: 1000, samples: 16000);
      g.finished();
      expect(g.shouldRun(nowMs: 1500, samples: 32000), isFalse); // too soon
      expect(g.shouldRun(nowMs: 1800, samples: 16500), isFalse); // not enough new audio
      expect(g.shouldRun(nowMs: 1800, samples: 21000), isTrue);
    });

    test('skips (does not queue) while the previous run is busy', () {
      final g = DraftGate();
      g.started(nowMs: 0, samples: 16000);
      expect(g.busy, isTrue);
      expect(g.shouldRun(nowMs: 10000, samples: 90000), isFalse);
      g.finished();
      expect(g.shouldRun(nowMs: 10000, samples: 90000), isTrue);
    });

    test('segmentReset counts audio from zero again', () {
      final g = DraftGate();
      g.started(nowMs: 0, samples: 80000);
      g.finished();
      g.segmentReset();
      expect(g.shouldRun(nowMs: 2000, samples: 9600), isTrue);
    });
  });

  group('DraftAudio', () {
    test('accumulates only while speech is detected, with pre-roll', () {
      final a = DraftAudio(prerollSamples: 4000);
      a.feed(chunk(2000, 0.5), detected: false);
      a.feed(chunk(2000, 0.5), detected: false);
      a.feed(chunk(3000, 0.5), detected: false);
      expect(a.active, isFalse);
      a.feed(chunk(1600, 0.9), detected: true);
      expect(a.active, isTrue);
      expect(a.length, 3000 + 1600); // the pre-roll keeps whole chunks up to 4000 samples
      a.feed(chunk(1600, 0.9), detected: true);
      expect(a.length, 3000 + 3200);
      a.feed(chunk(1600), detected: false);
      expect(a.active, isFalse);
      expect(a.length, 0);
    });

    test('segmentDone empties the buffer but keeps going', () {
      final a = DraftAudio();
      a.feed(chunk(16000), detected: true);
      a.segmentDone();
      expect(a.length, 0);
      a.feed(chunk(800), detected: true);
      expect(a.length, 800);
      expect(a.snapshot().length, 800);
    });

    test('is capped', () {
      final a = DraftAudio(maxSamples: 32000);
      for (var i = 0; i < 10; i++) {
        a.feed(chunk(8000), detected: true);
      }
      expect(a.length, lessThanOrEqualTo(32000));
      expect(a.snapshot().length, a.length);
    });
  });

  group('DraftTracker replace logic', () {
    test('final replaces the draft', () {
      final t = DraftTracker();
      t.onSpeech(true, 0);
      expect(t.onDraft(0, 'hello wor'), isTrue);
      expect(t.shown(500), 'hello wor');
      expect(t.onDraft(0, 'hello world'), isTrue);
      expect(t.onFinal(0), isTrue);
      expect(t.shown(900), '');
    });

    test('a late draft of a finalized segment is dropped', () {
      final t = DraftTracker();
      t.onSpeech(true, 0);
      t.onFinal(0);
      expect(t.onDraft(0, 'hello'), isFalse);
      expect(t.shown(100), '');
      expect(t.onDraft(1, 'next sentence'), isTrue);
      expect(t.shown(100), 'next sentence');
    });

    test('same text does not trigger a refresh', () {
      final t = DraftTracker();
      expect(t.onDraft(2, 'a'), isTrue);
      expect(t.onDraft(2, 'a'), isFalse);
    });

    test('draft is held after speech ends, then expires without a final', () {
      final t = DraftTracker();
      t.onSpeech(true, 0);
      t.onDraft(0, 'hmm');
      expect(t.onSpeech(false, 1000), isTrue);
      expect(t.shown(1000 + DraftTracker.holdAfterSpeechEndMs), 'hmm');
      expect(t.shown(1000 + DraftTracker.holdAfterSpeechEndMs + 1), '');
      expect(t.expiresAtMs(), 1000 + DraftTracker.holdAfterSpeechEndMs + 1);
    });

    test('empty final (nothing recognized) clears the draft too', () {
      final t = DraftTracker();
      t.onDraft(3, 'noise');
      expect(t.onFinal(3), isTrue);
      expect(t.text, '');
    });
  });

  group('FeaturePrefs', () {
    test('defaults: 原文草稿 on', () {
      expect(const FeaturePrefs().sourceDraft, isTrue);
      expect(FeaturePrefs.parse(null).sourceDraft, isTrue);
      expect(FeaturePrefs.parse('garbage').sourceDraft, isTrue);
    });

    test('round trip and unknown keys', () {
      expect(FeaturePrefs.parse(const FeaturePrefs(sourceDraft: false).format()).sourceDraft,
          isFalse);
      expect(FeaturePrefs.parse('x=1\nsourceDraft=0\n').sourceDraft, isFalse);
    });
  });
}
