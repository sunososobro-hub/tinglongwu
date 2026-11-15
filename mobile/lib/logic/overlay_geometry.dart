import 'dart:math' as math;

/// Window geometry of the floating subtitle overlay (#1078). Pure, so it is unit-testable;
/// `SubtitleOverlay.kt` (`fitToScreen`) implements the same algorithm for the real window.
class OverlayRect {
  const OverlayRect(this.x, this.y, this.width, this.height);
  final int x;
  final int y;
  final int width;
  final int height;
  int get right => x + width;
  int get bottom => y + height;
  @override
  bool operator ==(Object other) =>
      other is OverlayRect && other.x == x && other.y == y && other.width == width && other.height == height;
  @override
  int get hashCode => Object.hash(x, y, width, height);
  @override
  String toString() => 'OverlayRect($x, $y, $width, $height)';
}

/// Re-lays the overlay out after the screen size changed (rotation): the position is kept as a
/// fraction of the screen (so it follows the rotation), the width is capped to the screen, and
/// the window is moved fully inside [screenW] x [screenH]. [fx]/[fy] are the top-left corner as
/// fractions of the screen it was placed on; [widthFrac] is its width as a fraction of that
/// screen's width. [minWidth] is the narrowest allowed window.
OverlayRect fitOverlay({
  required int screenW,
  required int screenH,
  required double fx,
  required double fy,
  required double widthFrac,
  required int height,
  int minWidth = 0,
}) {
  final w = (screenW * widthFrac).round().clamp(math.min(minWidth, screenW), screenW).toInt();
  final h = math.min(height, screenH);
  final x = (screenW * fx).round().clamp(0, screenW - w).toInt();
  final y = (screenH * fy).round().clamp(0, screenH - h).toInt();
  return OverlayRect(x, y, w, h);
}
