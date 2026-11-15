import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/phone_audio.dart';

Uint8List pcm(int samples, int value) {
  final b = ByteData(samples * 2);
  for (var i = 0; i < samples; i++) {
    b.setInt16(i * 2, value, Endian.little);
  }
  return b.buffer.asUint8List();
}

void main() {
  group('SilenceWatch (#998)', () {
    test('hint only after 5 s of continuous silence', () {
      final w = SilenceWatch();
      for (var i = 0; i < 49; i++) {
        expect(w.feed(pcm(1600, 0)), isFalse); // 4.9 s
      }
      expect(w.feed(pcm(1600, 0)), isTrue); // 5.0 s
    });

    test('sound resets the counter', () {
      final w = SilenceWatch(hintAfterSeconds: 1);
      for (var i = 0; i < 10; i++) {
        w.feed(pcm(1600, 0));
      }
      expect(w.silent, isTrue);
      expect(w.feed(pcm(1600, 3000)), isFalse);
      expect(w.feed(pcm(1600, 0)), isFalse);
    });

    test('noise floor counts as silence, negative peaks as sound', () {
      final w = SilenceWatch(hintAfterSeconds: 0.1);
      expect(w.feed(pcm(1600, 40)), isTrue);
      expect(w.feed(pcm(1600, -2000)), isFalse);
      expect(w.feed(Uint8List(0)), isFalse);
    });
  });

  group('overlayText (#998)', () {
    test('newest two finished lines, oldest first', () {
      expect(overlayText(['a', null, 'b', ' c ', '']), 'b\nc');
    });
    test('one line and empty', () {
      expect(overlayText(['x'], maxLines: 1), 'x');
      expect(overlayText([null, '  ']), '');
    });
  });

  group('OverlayStyle (#998)', () {
    test('parse/format round trip and fallbacks', () {
      const s = OverlayStyle(fontSize: 24, opacity: 0.35);
      expect(OverlayStyle.parse(s.format()), s);
      expect(OverlayStyle.parse(null), const OverlayStyle());
      expect(OverlayStyle.parse('junk'), const OverlayStyle());
      expect(OverlayStyle.parse('99,-1'),
          const OverlayStyle(fontSize: OverlayStyle.maxFontSize, opacity: 0));
    });

    test('store saves and loads', () {
      final dir = Directory.systemTemp.createTempSync('tlwu_overlay');
      try {
        final st = OverlayStyleStore('${dir.path}/overlay_style.txt');
        expect(st.load(), const OverlayStyle());
        st.save(const OverlayStyle(fontSize: 30, opacity: 0.8));
        expect(st.load(), const OverlayStyle(fontSize: 30, opacity: 0.8));
      } finally {
        dir.deleteSync(recursive: true);
      }
    });
  });
}
