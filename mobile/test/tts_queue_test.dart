import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/speech_out.dart';
import 'package:mobile/services/tts_player.dart';

/// #1953: lines are spoken one at a time (never together, never cut), earphone-only policy.
void main() {
  final line = Float32List(16);

  group('SerialSpeaker', () {
    late List<String> log;
    late List<Completer<void>> ends;
    late SerialSpeaker s;
    var playing = 0;
    var maxPlaying = 0;

    setUp(() {
      log = [];
      ends = [];
      playing = 0;
      maxPlaying = 0;
      s = SerialSpeaker(
        start: (samples, rate) async {
          playing++;
          if (playing > maxPlaying) maxPlaying = playing;
          log.add('start$rate');
          return true;
        },
        finished: (ms) {
          final c = Completer<void>();
          ends.add(c);
          return c.future.then((_) {
            playing--;
            log.add('end');
          });
        },
      );
    });

    test('two lines never play at once: the second starts after the first ends', () async {
      final a = s.play(line, 1, 500);
      final b = s.play(line, 2, 500);
      expect(await a, isTrue);
      await pumpEventQueue();
      expect(log, ['start1']); // line 2 waits, line 1 is not cut
      ends[0].complete();
      expect(await b, isTrue);
      expect(log, ['start1', 'end', 'start2']);
      ends[1].complete();
      await s.done;
      expect(maxPlaying, 1);
    });

    test('stop drops lines still waiting', () async {
      final a = s.play(line, 1, 500);
      final b = s.play(line, 2, 500);
      await a;
      s.stop();
      ends[0].complete();
      expect(await b, isFalse);
      await s.done;
      expect(log, ['start1', 'end']);
    });

    test('a line that fails to start does not block the next', () async {
      var first = true;
      final f = SerialSpeaker(
        start: (_, _) async {
          final ok = !first;
          first = false;
          return ok;
        },
        finished: (_) async {},
      );
      expect(await f.play(line, 1, 10), isFalse);
      expect(await f.play(line, 1, 10), isTrue);
    });
  });

  group('earphone-only', () {
    test('listen mode speaks only into a headset; conversation always', () {
      expect(earphoneOnlyFor(conversation: false), isTrue);
      expect(earphoneOnlyFor(conversation: true), isFalse);
      expect(speakAllowed(headset: false, earphoneOnly: true), isFalse);
      expect(speakAllowed(headset: true, earphoneOnly: true), isTrue);
      expect(speakAllowed(headset: false, earphoneOnly: false), isTrue);
      expect(speakAllowed(headset: null, earphoneOnly: true), isTrue);
    });
  });

  group('TtsPlayer channel', () {
    TestWidgetsFlutterBinding.ensureInitialized();
    const channel = MethodChannel('tlwu/tts');
    final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    tearDown(() => messenger.setMockMethodCallHandler(channel, null));

    test('the second play call waits for the native done of the first', () async {
      final calls = <String>[];
      final done = Completer<void>();
      messenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call.method);
        if (call.method == 'done') {
          await done.future;
          return null;
        }
        if (call.method == 'headset') return true;
        return call.method == 'play' ? true : null;
      });
      final a = TtsPlayer.play(line, 16000, 2000);
      final b = TtsPlayer.play(line, 16000, 2000);
      expect(await a, isTrue);
      await pumpEventQueue();
      expect(calls, ['play', 'done']);
      done.complete();
      expect(await b, isTrue);
      expect(calls.take(3), ['play', 'done', 'play']);
      expect(calls.where((c) => c == 'play').length, 2);
      expect(await TtsPlayer.headset(), isTrue);
      await TtsPlayer.stop();
    });
  });
}
