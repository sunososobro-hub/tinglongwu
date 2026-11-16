/// Tilt-to-flip (#1146): decides when the subtitle screen should rotate 180° so the person
/// across the table can read it. Pure logic; the sensor stream (sensors_plus) feeds [update].
///
/// Input is the accelerometer gravity y axis in g (device portrait: y ≈ +1 upright,
/// y ≈ -1 upside-down). Hysteresis: flip needs y < [flipBelow] held for [hold]; flip back needs
/// y > [unflipAbove] held for [hold]. In between (flat on the table) the state is kept.
class FlipDetector {
  FlipDetector({
    this.flipBelow = -0.5,
    this.unflipAbove = 0.5,
    this.hold = const Duration(milliseconds: 500),
    this.locked = false,
    bool flipped = false,
  }) : _flipped = flipped;

  final double flipBelow;
  final double unflipAbove;
  final Duration hold;

  /// When true the state never changes (user lock toggle).
  bool locked;

  bool _flipped;
  bool get flipped => _flipped;

  Duration? _candidateSince;
  bool? _candidate;

  /// Feed one sample taken at [at] (monotonic time). Returns the current flipped state.
  bool update(double y, Duration at) {
    if (locked) {
      _candidate = null;
      return _flipped;
    }
    bool? want;
    if (y < flipBelow) {
      want = true;
    } else if (y > unflipAbove) {
      want = false;
    }
    if (want == null || want == _flipped) {
      _candidate = null; // dead zone or already in that state: restart the timer
      return _flipped;
    }
    if (_candidate != want) {
      _candidate = want;
      _candidateSince = at;
      return _flipped;
    }
    if (at - _candidateSince! >= hold) {
      _flipped = want;
      _candidate = null;
    }
    return _flipped;
  }
}
