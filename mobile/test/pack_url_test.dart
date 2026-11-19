import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/pack_url.dart';

// #1567: the downloader may fetch only this repo's release assets.
void main() {
  test('accepts this repo\'s release assets', () {
    expect(isAllowedPackUrl('${packReleasePrefix}mobile-latest/tlwu-voice-en.zip'), isTrue);
  });

  test('rejects other hosts, repos, http and path tricks', () {
    expect(isAllowedPackUrl('https://example.com/tlwu-voice-en.zip'), isFalse);
    expect(isAllowedPackUrl('https://github.com/other/repo/releases/download/x/a.zip'), isFalse);
    expect(isAllowedPackUrl('http://github.com/sunososobro-hub/tinglongwu/releases/download/x/a.zip'), isFalse);
    expect(isAllowedPackUrl('$packReleasePrefix../../x'), isFalse);
  });
}
