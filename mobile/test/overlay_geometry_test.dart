import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/overlay_geometry.dart';

void main() {
  bool inside(OverlayRect r, int w, int h) =>
      r.x >= 0 && r.y >= 0 && r.right <= w && r.bottom <= h && r.width > 0 && r.height > 0;

  test('portrait -> landscape -> portrait stays inside the screen', () {
    // Portrait 1080x2400, default placement: 4% left, 70% down, 92% wide.
    var r = fitOverlay(
        screenW: 1080, screenH: 2400, fx: 0.04, fy: 0.70, widthFrac: 0.92, height: 300);
    expect(inside(r, 1080, 2400), isTrue);
    // Rotate to landscape 2400x1080: raw y = 1680 would be off screen.
    final fy = r.y / 2400, fx = r.x / 1080, wf = r.width / 1080;
    r = fitOverlay(screenW: 2400, screenH: 1080, fx: fx, fy: fy, widthFrac: wf, height: 300);
    expect(inside(r, 2400, 1080), isTrue);
    expect(r.bottom, lessThanOrEqualTo(1080));
    // Back to portrait.
    r = fitOverlay(
        screenW: 1080, screenH: 2400, fx: r.x / 2400, fy: r.y / 1080, widthFrac: r.width / 2400,
        height: 300);
    expect(inside(r, 1080, 2400), isTrue);
  });

  test('height taller than the screen is capped, width never exceeds the screen', () {
    final r = fitOverlay(
        screenW: 500, screenH: 400, fx: 0.9, fy: 0.9, widthFrac: 3.0, height: 900);
    expect(r, const OverlayRect(0, 0, 500, 400));
  });

  test('minWidth is honoured when it fits', () {
    final r = fitOverlay(
        screenW: 1000, screenH: 1000, fx: 0, fy: 0, widthFrac: 0.05, height: 10, minWidth: 200);
    expect(r.width, 200);
  });
}
