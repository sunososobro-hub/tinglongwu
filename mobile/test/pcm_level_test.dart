import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/pcm_level.dart';

Uint8List pcm(List<int> samples) {
  final b = ByteData(samples.length * 2);
  for (var i = 0; i < samples.length; i++) {
    b.setInt16(i * 2, samples[i], Endian.little);
  }
  return b.buffer.asUint8List();
}

void main() {
  test('empty and all-zero PCM are -120 dBFS', () {
    expect(pcm16RmsDb(Uint8List(0)), -120);
    expect(pcm16RmsDb(Uint8List(3200)), -120);
  });

  test('constant amplitude 1024 is 20*log10(1024/32768) = -30.1 dBFS', () {
    expect(pcm16RmsDb(pcm(List.filled(1600, 1024))), closeTo(-30.10, 0.01));
    expect(pcm16RmsDb(pcm(List.generate(1600, (i) => i.isEven ? 1024 : -1024))), closeTo(-30.10, 0.01));
  });

  test('little-endian: bytes 00 40 are +16384 (-6.02 dBFS), a trailing odd byte is ignored', () {
    expect(pcm16RmsDb(Uint8List.fromList([0x00, 0x40, 0x00, 0x40, 0x7f])), closeTo(-6.02, 0.01));
  });
}
