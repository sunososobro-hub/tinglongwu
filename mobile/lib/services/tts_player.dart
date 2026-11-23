import 'dart:typed_data';

import 'package:flutter/services.dart';

/// Plays synthesized speech on the phone speaker (#558): Android AudioTrack in TtsPlayer.kt,
/// no new plugin and no network use. iOS has no native side yet: [play] returns false.
class TtsPlayer {
  static const _channel = MethodChannel('tlwu/tts');

  /// Starts playing [samples] (mono float, [-1, 1]) and returns once the audio has started
  /// (true) or could not play (false). Replaces anything still playing.
  static Future<bool> play(Float32List samples, int sampleRate) async {
    try {
      return await _channel.invokeMethod<bool>('play', {'samples': samples, 'sampleRate': sampleRate}) ??
          false;
    } catch (_) {
      return false; // no native side (iOS, tests) or the audio device refused
    }
  }

  static Future<void> stop() async {
    try {
      await _channel.invokeMethod<void>('stop');
    } catch (_) {}
  }
}
