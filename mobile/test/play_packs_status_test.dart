import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/play_packs.dart';

// #1169: the internal-track AAB has no model packs. Play may still answer "available" for it, with nothing to
// download; the app must then fall back to importing the models zip instead of waiting for a Play download.
void main() {
  test('Play install with the model packs: available', () {
    expect(PlayPacks.packsAvailable({'available': true, 'totalBytes': 1345000000, 'paths': {}}), isTrue);
  });

  test('Play install of a build without packs (0 bytes to fetch): not available, import the zip', () {
    expect(PlayPacks.packsAvailable({'available': true, 'totalBytes': 0, 'paths': {}}), isFalse);
    expect(PlayPacks.packsAvailable({'available': true, 'paths': {}}), isFalse);
  });

  test('side-loaded APK, Play error or no answer: not available', () {
    expect(PlayPacks.packsAvailable({'available': false, 'error': 'x'}), isFalse);
    expect(PlayPacks.packsAvailable({'available': false, 'totalBytes': 5}), isFalse);
    expect(PlayPacks.packsAvailable(null), isFalse);
  });
}
