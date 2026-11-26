import 'dart:typed_data';

import 'package:flutter/services.dart';

import '../logic/speech_out.dart';

/// Plays synthesized speech on the phone speaker (#558): Android AudioTrack in TtsPlayer.kt,
/// no new plugin and no network use. iOS has no native side yet: [play] returns false.
/// One line at a time (#1953): a new line waits for the one playing; while a line plays the
/// native side holds transient audio focus with ducking, so a playing video gets quieter.
class TtsPlayer {
  static const _channel = MethodChannel('tlwu/tts');

  static final _serial = SerialSpeaker(start: _start, finished: _finished);

  /// Queues [samples] (mono float, [-1, 1], [durationMs] long) after the line still playing and
  /// returns once it has started (true) or could not play (false).
  static Future<bool> play(Float32List samples, int sampleRate, int durationMs) =>
      _serial.play(samples, sampleRate, durationMs);

  /// Completes when every queued line has finished playing.
  static Future<void> get done => _serial.done;

  static Future<bool> _start(Float32List samples, int sampleRate) async {
    try {
      return await _channel.invokeMethod<bool>('play', {'samples': samples, 'sampleRate': sampleRate}) ??
          false;
    } catch (_) {
      return false; // no native side (iOS, tests) or the audio device refused
    }
  }

  /// Waits for the native end-of-line signal; falls back to the line's length (plus a margin)
  /// when the native side cannot say.
  static Future<void> _finished(int durationMs) async {
    final fallback = Duration(milliseconds: durationMs + 1000);
    try {
      await _channel.invokeMethod<void>('done').timeout(fallback);
    } on MissingPluginException {
      await Future<void>.delayed(Duration(milliseconds: durationMs));
    } catch (_) {}
  }

  /// Whether a headset (wired, USB or Bluetooth) is the output; null when unknown.
  static Future<bool?> headset() async {
    try {
      return await _channel.invokeMethod<bool>('headset');
    } catch (_) {
      return null;
    }
  }

  static Future<void> stop() async {
    _serial.stop();
    try {
      await _channel.invokeMethod<void>('stop');
    } catch (_) {}
  }
}
