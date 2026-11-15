import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/speech_out.dart';

void main() {
  group('TtsVoicePaths (#558 read-aloud voice layout)', () {
    test('finds a complete voice through the resolver', () {
      final v = TtsVoicePaths.find('en', resolve: (r) => '/m/$r', exists: (_) => true)!;
      expect(v.lang, 'en');
      expect(v.model, '/m/tts-en/model.onnx');
      expect(v.tokens, '/m/tts-en/tokens.txt');
      expect(v.lexicon, '/m/tts-en/lexicon.txt');
    });

    test('a partial voice counts as not installed', () {
      final v = TtsVoicePaths.find('en',
          resolve: (r) => '/m/$r', exists: (p) => !p.endsWith('lexicon.txt'));
      expect(v, isNull);
    });

    test('each language has its own folder', () {
      expect(TtsVoiceLayout.rel('ru'), ['tts-ru/model.onnx', 'tts-ru/tokens.txt', 'tts-ru/lexicon.txt']);
    });
  });

  group('EchoGuard', () {
    test('mutes the mic while a line plays plus the tail, then lets audio through', () {
      final g = EchoGuard();
      final pcm = Uint8List.fromList([1, 2, 3, 4]);
      expect(g.filter(pcm, 0), same(pcm));
      g.playing(startMs: 1000, durationMs: 2000);
      expect(g.filter(pcm, 1500), Uint8List(4)); // silence, same length
      expect(g.muted(3000 + EchoGuard.tailMs - 1), isTrue);
      expect(g.muted(3000 + EchoGuard.tailMs), isFalse);
      expect(g.filter(pcm, 3000 + EchoGuard.tailMs), same(pcm));
    });

    test('an early stop keeps only the tail', () {
      final g = EchoGuard()..playing(startMs: 0, durationMs: 5000);
      g.stopped(1000);
      expect(g.muted(1000 + EchoGuard.tailMs - 1), isTrue);
      expect(g.muted(1000 + EchoGuard.tailMs), isFalse);
    });

    test('a shorter second line never shortens the guard', () {
      final g = EchoGuard()
        ..playing(startMs: 0, durationMs: 4000)
        ..playing(startMs: 100, durationMs: 100);
      expect(g.muted(3000), isTrue);
    });
  });

  group('SpeakQueue', () {
    test('speaks in order and skips text without letters', () {
      final q = SpeakQueue();
      expect(q.add(const Utterance('  Hello.  ', lang: 'en', speechEndMs: 1)), isTrue);
      expect(q.add(const Utterance('…!', lang: 'en', speechEndMs: 2)), isFalse);
      expect(q.add(const Utterance('Thanks', lang: 'en', speechEndMs: 3)), isTrue);
      final a = q.next()!;
      expect(a.text, 'Hello.');
      expect(a.speechEndMs, 1);
      expect(q.next()!.text, 'Thanks');
      expect(q.next(), isNull);
    });

    test('drops the oldest lines when the voice falls behind', () {
      final q = SpeakQueue();
      for (var i = 0; i < SpeakQueue.maxPending + 2; i++) {
        q.add(Utterance('line $i', lang: 'en', speechEndMs: i));
      }
      expect(q.length, SpeakQueue.maxPending);
      expect(q.dropped, 2);
      expect(q.next()!.text, 'line 2');
    });
  });

  test('voice status label', () {
    expect(voiceStatusLabel(installed: false, speaking: false), '未安裝語音包，只顯示文字');
    expect(voiceStatusLabel(installed: true, speaking: true), '朗讀中');
    expect(voiceStatusLabel(installed: true, speaking: false), '朗讀');
  });
}
