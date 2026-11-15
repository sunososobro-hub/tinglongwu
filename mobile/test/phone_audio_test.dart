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

  group('displayLines / SubtitleMode (#998)', () {
    const subs = [
      ('Hello and welcome.', '你好，歡迎。'),
      ('I am Gary.', '我是 Gary。'),
      ('Still translating', null),
    ];

    test('zh: finished translations only, newest two', () {
      expect(displayLines(subs, SubtitleMode.zh), const [
        SubtitleLine(translation: '你好，歡迎。'),
        SubtitleLine(translation: '我是 Gary。'),
      ]);
    });

    test('source: every recognised line, no wait for the translation', () {
      expect(displayLines(subs, SubtitleMode.source), const [
        SubtitleLine(source: 'I am Gary.'),
        SubtitleLine(source: 'Still translating'),
      ]);
    });

    test('both: source above translation, finished lines only', () {
      expect(displayLines(subs, SubtitleMode.both, maxEntries: 1), const [
        SubtitleLine(source: 'I am Gary.', translation: '我是 Gary。'),
      ]);
    });

    test('both: passthrough (source == translation) shows it once', () {
      expect(displayLines(const [(' Hi there ', 'Hi there')], SubtitleMode.both),
          const [SubtitleLine(translation: 'Hi there')]);
    });

    test('blank entries are skipped', () {
      expect(displayLines(const [('  ', '  '), ('', null)], SubtitleMode.both), isEmpty);
      expect(displayLines(const [('  ', null)], SubtitleMode.source), isEmpty);
    });

    test('parse: names, case, fallback to both', () {
      expect(SubtitleMode.parse('zh'), SubtitleMode.zh);
      expect(SubtitleMode.parse(' SOURCE '), SubtitleMode.source);
      expect(SubtitleMode.parse(null), SubtitleMode.both);
      expect(SubtitleMode.parse('x'), SubtitleMode.both);
      expect(SubtitleMode.fallback, SubtitleMode.both);
      expect([for (final m in SubtitleMode.values) m.label], ['中文', '原文', '雙語']);
    });

    test('mode is saved with the overlay style; old two-field files default to both', () {
      const s = OverlayStyle(fontSize: 18, opacity: 0.5, mode: SubtitleMode.source);
      expect(s.format(), '18.0,0.50,source');
      expect(OverlayStyle.parse(s.format()), s);
      expect(OverlayStyle.parse('22.0,0.40').mode, SubtitleMode.both);
      expect(const OverlayStyle().mode, SubtitleMode.both);
      expect(s.copyWith(fontSize: 30).mode, SubtitleMode.source);
      expect(s.copyWith(mode: SubtitleMode.zh),
          const OverlayStyle(fontSize: 18, opacity: 0.5, mode: SubtitleMode.zh));
    });
  });

  group('CaptureModeText (#998)', () {
    test('plain-word labels and hints, no mic / phone-audio jargon', () {
      expect(CaptureModeText.label(phone: false), '說話翻譯');
      expect(CaptureModeText.hint(phone: false), '翻譯身邊的人說的話');
      expect(CaptureModeText.label(phone: true), '看影片翻譯');
      expect(CaptureModeText.hint(phone: true), '翻譯手機正在播的影片，字幕浮在畫面上');
      for (final s in [
        CaptureModeText.micLabel,
        CaptureModeText.micHint,
        CaptureModeText.phoneLabel,
        CaptureModeText.phoneHint
      ]) {
        expect(s.contains('麥克風') || s.contains('音訊'), isFalse);
      }
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
