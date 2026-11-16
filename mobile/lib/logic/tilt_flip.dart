/// 傾斜翻面 (#1146): decides when to show the screen upside-down to the other person, from
/// accelerometer samples (gravity included). Pure Dart; thresholds are named so they can be tuned.
class TiltFlip {
  /// Standard gravity, m/s^2 (sensors_plus reports m/s^2).
  static const gravity = 9.80665;

  /// Enter listener view when smoothed y < this (g): phone upside-down portrait.
  static const enterY = -0.5;

  /// Leave listener view when smoothed y > this (g). Higher than [enterY] = hysteresis.
  static const exitY = -0.2;

  /// The condition must hold this long before flipping (ms); filters walking jitter.
  static const holdMs = 500;

  /// Phone lying flat (|z| dominates, above this in g) keeps the last state.
  static const flatZ = 0.75;

  /// Low-pass weight of the newest sample (0..1); smaller = smoother.
  static const smoothing = 0.25;
}

class TiltFlipDetector {
  bool _flipped = false;
  double? _y;
  int? _pendingSince;
  bool _pendingTarget = false;

  /// True while the screen should be shown to the listener (rotated 180°).
  bool get flipped => _flipped;

  /// Feed one accelerometer sample in m/s^2 at [nowMs]; returns [flipped].
  bool feed(double x, double y, double z, int nowMs) {
    const g = TiltFlip.gravity;
    final gy = y / g, gz = z / g;
    _y = _y == null ? gy : _y! + TiltFlip.smoothing * (gy - _y!);
    final sy = _y!;
    // Flat: keep state and drop any half-counted change.
    if (gz.abs() >= TiltFlip.flatZ && gz.abs() > sy.abs()) {
      _pendingSince = null;
      return _flipped;
    }
    final bool? want = _flipped
        ? (sy > TiltFlip.exitY ? false : null)
        : (sy < TiltFlip.enterY ? true : null);
    if (want == null) {
      _pendingSince = null;
      return _flipped;
    }
    if (_pendingSince == null || _pendingTarget != want) {
      _pendingSince = nowMs;
      _pendingTarget = want;
    }
    if (nowMs - _pendingSince! >= TiltFlip.holdMs) {
      _flipped = want;
      _pendingSince = null;
    }
    return _flipped;
  }

  void reset() {
    _flipped = false;
    _y = null;
    _pendingSince = null;
  }
}
