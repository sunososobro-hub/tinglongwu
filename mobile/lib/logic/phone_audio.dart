import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

/// Pure helpers for phone-audio subtitles (#998): silence detection, overlay text and the
/// overlay style file. No Flutter imports, so they are unit-testable.

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
String overlayText(Iterable<String?> translations, {int maxLines = 2}) {
  final done = [
    for (final t in translations)
      if (t != null && t.trim().isNotEmpty) t.trim()
  ];
  return done.sublist(math.max(0, done.length - maxLines)).join('\n');
}

/// Overlay font size (sp) and background opacity (0..1), kept in a one-line text file.
class OverlayStyle {
  const OverlayStyle({this.fontSize = defaultFontSize, this.opacity = defaultOpacity});

  static const defaultFontSize = 20.0;
  static const defaultOpacity = 0.6;
  static const minFontSize = 12.0;
  static const maxFontSize = 40.0;

  final double fontSize;
  final double opacity;

  OverlayStyle clamp() => OverlayStyle(
        fontSize: fontSize.clamp(minFontSize, maxFontSize).toDouble(),
        opacity: opacity.clamp(0.0, 1.0).toDouble(),
      );

  /// "20.0,0.60"
  String format() => '${fontSize.toStringAsFixed(1)},${opacity.toStringAsFixed(2)}';

  /// Bad or missing values fall back to the defaults.
  static OverlayStyle parse(String? s) {
    final parts = (s ?? '').trim().split(',');
    final f = parts.isNotEmpty ? double.tryParse(parts[0].trim()) : null;
    final o = parts.length > 1 ? double.tryParse(parts[1].trim()) : null;
    return OverlayStyle(
      fontSize: (f == null || f.isNaN) ? defaultFontSize : f,
      opacity: (o == null || o.isNaN) ? defaultOpacity : o,
    ).clamp();
  }

  @override
  bool operator ==(Object other) =>
      other is OverlayStyle && other.fontSize == fontSize && other.opacity == opacity;

  @override
  int get hashCode => Object.hash(fontSize, opacity);
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
