/// Loudness gate for two-way (conversation) mode (#1479). Pure Dart, no Flutter imports.
///
/// Silero VAD ignores loudness: a quiet background "да" scores like a close one. This gate drops a
/// SHORT segment (at most [maxShortSec]) when it is clearly quieter than the people talking to the
/// phone: below the absolute [floorDb], or more than [relativeDb] under the loudest recently kept
/// segment. Long segments are never dropped. Values come from `mobile/tool/vad_replay.py`
/// (clips: foreground -21 dBFS RMS, background words -37..-40 dBFS): background-word bubbles
/// 30 -> 2, 13/13 foreground sentences and the same 5/6 short answers survive. Re-run the replay
/// before changing them.
library;

import 'dart:math' show ln10, log;

class LevelGate {
  LevelGate();

  /// A segment longer than this (s) is never dropped.
  static const maxShortSec = 1.5;

  /// Absolute floor (dBFS RMS) for a short segment; background words measured -37..-40,
  /// foreground speech -21.
  static const floorDb = -34.0;

  /// A short segment this many dB under the recent loud reference is dropped.
  static const relativeDb = 10.0;

  /// The reference is the loudest of the last [refCount] kept segments within [refWindowSec].
  static const refCount = 8;
  static const refWindowSec = 30.0;

  final List<(double, double)> _kept = []; // (time s, level dBFS)

  /// RMS level in dBFS of 16 kHz float samples in [-1, 1] (-120 for empty / silent).
  static double rmsDb(List<double> samples) {
    if (samples.isEmpty) return -120;
    var sum = 0.0;
    for (final s in samples) {
      sum += s * s;
    }
    final rms = sum / samples.length;
    if (rms <= 1e-12) return -120;
    return 10 * _log10(rms);
  }

  static double _log10(double x) => log(x) / ln10;

  /// True when the segment should be dropped. [nowSec] is any monotonic clock in seconds.
  bool shouldDrop({required double levelDb, required double durSec, required double nowSec}) {
    _kept.removeWhere((e) => nowSec - e.$1 > refWindowSec);
    var ref = -999.0;
    for (final e in _kept) {
      if (e.$2 > ref) ref = e.$2;
    }
    final drop = durSec <= maxShortSec && (levelDb < floorDb || levelDb < ref - relativeDb);
    if (!drop) {
      _kept.add((nowSec, levelDb));
      while (_kept.length > refCount) {
        _kept.removeAt(0);
      }
    }
    return drop;
  }
}
