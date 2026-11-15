import 'dart:async';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import '../logic/speech_out.dart';

/// One synthesized line: mono float samples in [-1, 1].
class TtsAudio {
  const TtsAudio(this.samples, this.sampleRate, this.genMs);
  final Float32List samples;
  final int sampleRate;

  /// Wall time of the synthesis.
  final int genMs;

  int get durationMs => sampleRate > 0 ? samples.length * 1000 ~/ sampleRate : 0;
}

/// sherpa-onnx offline TTS (#558) in a background isolate, so synthesis never blocks the UI.
/// One request at a time ([synth] callers await the previous result first).
class TtsWorker {
  TtsWorker._(this._isolate, this._toWorker, this._replies);

  final Isolate _isolate;
  final SendPort _toWorker;
  final StreamIterator<Object?> _replies;

  static Future<TtsWorker> spawn(TtsVoicePaths voice, {int numThreads = 2}) async {
    final fromWorker = ReceivePort();
    final iso = await Isolate.spawn(_entry, [fromWorker.sendPort, voice.model, voice.tokens, voice.lexicon, numThreads]);
    final replies = StreamIterator<Object?>(fromWorker);
    if (!await replies.moveNext()) throw StateError('TTS isolate exited');
    final first = replies.current;
    if (first is String) {
      iso.kill();
      throw StateError(first); // init error
    }
    final port = first as SendPort;
    return TtsWorker._(iso, port, replies);
  }

  /// Synthesizes [text]; throws on a synthesis error, returns no samples when nothing was said.
  Future<TtsAudio> synth(String text) async {
    _toWorker.send(text);
    if (!await _replies.moveNext()) throw StateError('TTS isolate exited');
    final r = _replies.current;
    if (r is String) throw StateError(r);
    final m = r as List;
    return TtsAudio((m[0] as TransferableTypedData).materialize().asFloat32List(), m[1] as int, m[2] as int);
  }

  void dispose() {
    _toWorker.send(null);
    _isolate.kill(priority: Isolate.beforeNextEvent);
    _replies.cancel();
  }

  static void _entry(List<Object> args) {
    final out = args[0] as SendPort;
    sherpa.OfflineTts? tts;
    try {
      sherpa.initBindings();
      tts = sherpa.OfflineTts(sherpa.OfflineTtsConfig(
        model: sherpa.OfflineTtsModelConfig(
          vits: sherpa.OfflineTtsVitsModelConfig(
            model: args[1] as String,
            tokens: args[2] as String,
            lexicon: args[3] as String,
          ),
          numThreads: args[4] as int,
          provider: 'cpu',
          debug: false,
        ),
      ));
    } catch (e) {
      out.send('TTS init failed: $e');
      return;
    }
    final inPort = ReceivePort();
    out.send(inPort.sendPort);
    inPort.listen((m) {
      if (m == null) {
        tts?.free();
        inPort.close();
        return;
      }
      try {
        final sw = Stopwatch()..start();
        final a = tts!.generate(text: m as String, sid: 0, speed: 1.0);
        out.send([TransferableTypedData.fromList([a.samples]), a.sampleRate, sw.elapsedMilliseconds]);
      } catch (e) {
        out.send('TTS failed: $e');
      }
    });
  }
}
