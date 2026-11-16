import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/speakers.dart';

// Mirrors tests/test_speakers.py (desktop).
void main() {
  test('same vector, same speaker', () {
    final c = SpeakerClusterer();
    expect(c.assign([1, 0, 0], 2), 1);
    expect(c.assign([2, 0, 0], 2), 1);
  });

  test('orthogonal vectors are new speakers, numbering increments', () {
    final c = SpeakerClusterer();
    expect(c.assign([1, 0, 0], 2), 1);
    expect(c.assign([0, 1, 0], 2), 2);
    expect(c.assign([0, 0, 1], 2), 3);
    expect(c.assign([0, 1, 0], 2), 2);
    expect(c.count, 3);
  });

  test('threshold boundary', () {
    final v = [0.8, 0.6, 0.0]; // cosine with [1,0,0] == 0.8
    expect(cosine(v, [1, 0, 0]), closeTo(0.8, 1e-12));
    var c = SpeakerClusterer(threshold: 0.8);
    c.assign([1, 0, 0], 2);
    expect(c.assign(v, 2), 1); // == threshold -> same
    c = SpeakerClusterer(threshold: 0.81);
    c.assign([1, 0, 0], 2);
    expect(c.assign(v, 2), 2); // just below -> new
  });

  test('desktop defaults: threshold 0.6, min 1 s', () {
    final c = SpeakerClusterer();
    expect(c.threshold, 0.6);
    expect(c.minSeconds, 1.0);
  });

  test('short sentence reuses the previous speaker', () {
    final c = SpeakerClusterer();
    c.assign([1, 0, 0], 2);
    c.assign([0, 1, 0], 2);
    expect(c.assign([1, 0, 0], 0.5), 2);
  });

  test('leading short sentence is unknown; exactly min counts', () {
    final c = SpeakerClusterer();
    expect(c.assign([1, 0, 0], 0.3), unknownSpeaker);
    expect(c.count, 0);
    expect(c.assign([1, 0, 0], 1.0), 1);
  });

  test('short sentence does not update the centroid', () {
    final c = SpeakerClusterer(threshold: 0.9);
    c.assign([1, 0, 0], 2);
    for (var i = 0; i < 20; i++) {
      c.assign([0, 1, 0], 0.5);
    }
    expect(c.count, 1);
    expect(c.assign([0, 1, 0], 2), 2);
  });

  test('configurable min seconds', () {
    final c = SpeakerClusterer(minSeconds: 3);
    expect(c.assign([1, 0, 0], 2), unknownSpeaker);
  });

  test('zero vector reuses last speaker; length mismatch throws', () {
    final c = SpeakerClusterer();
    expect(c.assign([0, 0, 0], 2), unknownSpeaker);
    c.assign([1, 0, 0], 2);
    expect(c.assign([0, 0, 0], 2), 1);
    expect(() => cosine([1, 0], [1]), throwsArgumentError);
    expect(cosine([0, 0], [1, 0]), 0);
  });

  test('label text', () {
    expect(speakerLabel(2), '說話者 2');
    expect(speakerLabel(unknownSpeaker), '');
  });
}
