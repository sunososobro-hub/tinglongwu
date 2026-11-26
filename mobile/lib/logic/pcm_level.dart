/// RMS level of 16-bit PCM (#1728: the emulator microphone check). Pure Dart, no Flutter imports.
library;

import 'dart:typed_data';

import 'level_gate.dart';

/// RMS level in dBFS of little-endian PCM16 bytes (-120 for empty / silent; a trailing odd byte is ignored).
double pcm16RmsDb(Uint8List bytes) {
  final b = ByteData.sublistView(bytes);
  final n = bytes.length ~/ 2;
  return LevelGate.rmsDb(
      List<double>.generate(n, (i) => b.getInt16(i * 2, Endian.little) / 32768.0, growable: false));
}
