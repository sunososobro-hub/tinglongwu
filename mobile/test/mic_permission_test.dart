import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/mic_permission.dart';

void main() {
  test('nothing is requested until ensure() is called', () {
    var asked = 0;
    MicPermission(isGranted: () async => false, request: () async { asked++; return true; });
    expect(asked, 0);
  });

  test('ensure() shows the rationale, then asks exactly once per start', () async {
    var asked = 0;
    final notes = <String>[];
    final m = MicPermission(
      isGranted: () async => false,
      request: () async { asked++; return true; },
      onRationale: notes.add,
    );
    expect(await m.ensure(), isTrue);
    expect(await m.ensure(), isTrue);
    expect(asked, 1);
    expect(notes, [MicPermission.rationale]);
  });

  test('denied: false and asks again on the next start', () async {
    var asked = 0;
    final m = MicPermission(isGranted: () async => false, request: () async { asked++; return false; });
    expect(await m.ensure(), isFalse);
    expect(await m.ensure(), isFalse);
    expect(asked, 2);
  });

  test('already granted: no dialog, no rationale', () async {
    final notes = <String>[];
    final m = MicPermission(
        isGranted: () async => true, request: () async => fail('no prompt'), onRationale: notes.add);
    expect(await m.ensure(), isTrue);
    expect(m.requests, 0);
    expect(notes, isEmpty);
  });
}
