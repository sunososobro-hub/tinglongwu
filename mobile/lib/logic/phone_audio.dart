import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

/// Pure helpers for phone-audio subtitles (#998): silence detection, overlay text and the
/// overlay style file. No Flutter imports, so they are unit-testable.

/// Plain-word names of the two capture modes (owner: no "mic" / "phone audio" jargon).
/// Code keeps AudioSourceKind.mic / .phone; only these user-visible strings change.
class CaptureModeText {
  static const micLabel = '說話翻譯';
  static const micHint = '翻譯身邊的人說的話';
  static const phoneLabel = '看影片翻譯';
  static const phoneHint = '翻譯手機正在播的影片，字幕浮在畫面上';

  /// iOS (#1076): shown before the system broadcast sheet. DRM apps deliver silence there.
  static const iosStartExplain = '下一步會出現「螢幕廣播」視窗：請確認選了「聽龍字幕」，再按「開始廣播」。\n\n'
      '開始後切到 YouTube 等 App 播影片，字幕會在子母畫面小視窗裡顯示。'
      '要停止：回到聽龍切回「說話翻譯」，或點螢幕上方的紅色錄影圖示。\n\n'
      'iOS 會把它當成螢幕錄影，但聽龍只取聲音，在手機上辨識翻譯，不會存檔或上傳。\n\n'
      'Netflix 等有版權保護的 App 可能只會傳來靜音，這時不會有字幕。';
  static const iosStartButton = '開始';

  static String label({required bool phone}) => phone ? phoneLabel : micLabel;
  static String hint({required bool phone}) => phone ? phoneHint : micHint;
}

/// Which native capture path this device has (#998 Android, #1076 iOS). Pure, so the per-platform
/// behaviour is unit-testable; [current] reads dart:io Platform.
enum CapturePlatform {
  /// AudioPlaybackCapture + "display over other apps" window (Android 10+).
  android,

  /// ReplayKit broadcast extension + Picture-in-Picture subtitles (iOS 15+).
  ios,

  /// Desktop / tests: no capture.
  other;

  static CapturePlatform current() => Platform.isAndroid
      ? CapturePlatform.android
      : Platform.isIOS
          ? CapturePlatform.ios
          : CapturePlatform.other;

  /// Whether to ask the native side at all.
  bool get mayCapture => this != CapturePlatform.other;

  /// iOS shows its own explanation (broadcast sheet + PiP) before starting; Android asks for the
  /// overlay permission instead.
  bool get explainsBeforeStart => this == CapturePlatform.ios;

  /// The overlay opacity slider: the iOS PiP window is always opaque.
  bool get overlayOpacityAdjustable => this == CapturePlatform.android;

  String get unsupportedText => switch (this) {
        CapturePlatform.ios => '這支手機不支援看影片翻譯（需要 iOS 15 以上）',
        _ => '這支手機不支援看影片翻譯（需要 Android 10 以上）',
      };

  /// Heading and help text of the overlay section in the subtitle settings sheet.
  String get overlayTitle => this == CapturePlatform.ios ? '子母畫面字幕' : '浮動字幕視窗';
  String get overlayHelp => this == CapturePlatform.ios
      ? '字幕顯示在子母畫面小視窗裡，可拖曳、用兩指縮放；滑到螢幕邊緣可暫時收起。'
      : '拖曳字幕可移動，拖曳右下角 ⇲ 可調寬度，高度隨內容增加。點一下回到聽龍。';
}

/// Detects "capture yields only silence" (an app that opts out of playback capture, e.g. DRM
/// video, or nothing playing) on a 16 kHz mono PCM16 stream.
class SilenceWatch {
  SilenceWatch({this.sampleRate = 16000, this.hintAfterSeconds = 5.0, this.peakThreshold = 64});

  final int sampleRate;
  final double hintAfterSeconds;

  /// Absolute PCM16 peak at or below which a chunk counts as silent (~ -54 dBFS).
  final int peakThreshold;

  int _silentSamples = 0;

  /// True once [hintAfterSeconds] of continuous silence has been fed.
  bool get silent => _silentSamples >= (hintAfterSeconds * sampleRate).round();

  /// Feeds PCM16 little-endian bytes; returns [silent] after this chunk.
  bool feed(Uint8List bytes) {
    final n = bytes.lengthInBytes ~/ 2;
    if (n == 0) return silent;
    final bd = ByteData.sublistView(bytes);
    var peak = 0;
    for (var i = 0; i < n; i++) {
      final v = bd.getInt16(i * 2, Endian.little).abs();
      if (v > peak) peak = v;
    }
    if (peak > peakThreshold) {
      _silentSamples = 0;
    } else {
      _silentSamples += n;
    }
    return silent;
  }

  void reset() => _silentSamples = 0;
}

/// Overlay content: the newest [maxLines] finished translations, oldest first, one per line.
String overlayText(Iterable<String?> translations, {int maxLines = 2}) => displayLines(
      [for (final t in translations) ('', t)],
      SubtitleMode.zh,
      maxEntries: maxLines,
    ).map((l) => l.translation).join('\n');

/// What the subtitles show (in the app and the floating overlay): the translation only, the
/// recognised source text only, or both (source on top, smaller). Default: both.
enum SubtitleMode {
  zh('中文'),
  source('原文'),
  both('雙語');

  const SubtitleMode(this.label);
  final String label;

  static const fallback = SubtitleMode.both;

  /// Unknown / empty / null fall back to [fallback].
  static SubtitleMode parse(String? s) {
    final c = (s ?? '').trim().toLowerCase();
    for (final m in values) {
      if (m.name == c) return m;
    }
    return fallback;
  }
}

/// One displayed subtitle: [source] (ASR text) and/or [translation]; null = not shown.
class SubtitleLine {
  const SubtitleLine({this.source, this.translation});
  final String? source;
  final String? translation;

  @override
  bool operator ==(Object other) =>
      other is SubtitleLine && other.source == source && other.translation == translation;

  @override
  int get hashCode => Object.hash(source, translation);

  @override
  String toString() => 'SubtitleLine($source | $translation)';
}

/// The newest [maxEntries] subtitles to display for [mode], oldest first, from
/// (source, translation) pairs (translation null = still translating).
/// - zh: finished translations only.
/// - source: every recognised line, no wait for the translation.
/// - both: finished lines with the source above the translation; the source is dropped when it
///   equals the translation (source language == subtitle language passthrough).
List<SubtitleLine> displayLines(Iterable<(String, String?)> subs, SubtitleMode mode,
    {int maxEntries = 2}) {
  final out = <SubtitleLine>[];
  for (final (rawSrc, rawTr) in subs) {
    final src = rawSrc.trim();
    final tr = rawTr?.trim() ?? '';
    switch (mode) {
      case SubtitleMode.zh:
        if (tr.isNotEmpty) out.add(SubtitleLine(translation: tr));
      case SubtitleMode.source:
        if (src.isNotEmpty) out.add(SubtitleLine(source: src));
      case SubtitleMode.both:
        if (tr.isNotEmpty) {
          out.add(SubtitleLine(source: src.isEmpty || src == tr ? null : src, translation: tr));
        }
    }
  }
  return out.sublist(math.max(0, out.length - maxEntries));
}

/// Overlay font size (sp), background opacity (0..1) and [SubtitleMode], kept in a one-line
/// text file ("20.0,0.60,both"; older two-field files load with the default mode).
class OverlayStyle {
  const OverlayStyle(
      {this.fontSize = defaultFontSize, this.opacity = defaultOpacity, this.mode = SubtitleMode.fallback});

  static const defaultFontSize = 20.0;
  static const defaultOpacity = 0.6;
  static const minFontSize = 12.0;
  static const maxFontSize = 40.0;

  final double fontSize;
  final double opacity;
  final SubtitleMode mode;

  /// Newest-line size (sp) on the in-app subtitle screen, driven by the same persisted
  /// [fontSize] as the overlay (#1079). Default 20 -> 38 portrait / 46 landscape (the old
  /// fixed sizes); the slider range 12..40 gives 23..76 portrait.
  double screenFontSize({required bool landscape}) => fontSize * (landscape ? 2.3 : 1.9);

  /// Pending source line and live draft size (sp) on the in-app subtitle screen (#1092):
  /// follows [fontSize]; default 20 -> 22 (the old fixed size).
  double get secondaryFontSize => fontSize * 1.1;

  OverlayStyle copyWith({double? fontSize, double? opacity, SubtitleMode? mode}) => OverlayStyle(
        fontSize: fontSize ?? this.fontSize,
        opacity: opacity ?? this.opacity,
        mode: mode ?? this.mode,
      );

  OverlayStyle clamp() => OverlayStyle(
        fontSize: fontSize.clamp(minFontSize, maxFontSize).toDouble(),
        opacity: opacity.clamp(0.0, 1.0).toDouble(),
        mode: mode,
      );

  /// "20.0,0.60,both"
  String format() => '${fontSize.toStringAsFixed(1)},${opacity.toStringAsFixed(2)},${mode.name}';

  /// Bad or missing values fall back to the defaults.
  static OverlayStyle parse(String? s) {
    final parts = (s ?? '').trim().split(',');
    final f = parts.isNotEmpty ? double.tryParse(parts[0].trim()) : null;
    final o = parts.length > 1 ? double.tryParse(parts[1].trim()) : null;
    return OverlayStyle(
      fontSize: (f == null || f.isNaN) ? defaultFontSize : f,
      opacity: (o == null || o.isNaN) ? defaultOpacity : o,
      mode: SubtitleMode.parse(parts.length > 2 ? parts[2] : null),
    ).clamp();
  }

  @override
  bool operator ==(Object other) =>
      other is OverlayStyle &&
      other.fontSize == fontSize &&
      other.opacity == opacity &&
      other.mode == mode;

  @override
  int get hashCode => Object.hash(fontSize, opacity, mode);
}

class OverlayStyleStore {
  OverlayStyleStore(this.path);
  final String path;

  OverlayStyle load() {
    try {
      return OverlayStyle.parse(File(path).readAsStringSync());
    } catch (_) {
      return const OverlayStyle();
    }
  }

  void save(OverlayStyle s) {
    try {
      File(path).writeAsStringSync(s.clamp().format());
    } catch (_) {}
  }
}
