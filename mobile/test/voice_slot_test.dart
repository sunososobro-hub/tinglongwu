import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/voice_slot.dart';

void main() {
  late List<String> disposed;
  late VoiceSlot<String> slot;
  setUp(() {
    disposed = [];
    slot = VoiceSlot<String>(dispose: disposed.add);
  });

  test('loads the voice once for concurrent requests', () async {
    var loads = 0;
    final c = Completer<String?>();
    Future<String?> load() {
      loads++;
      return c.future;
    }

    final a = slot.ensure('en', load);
    final b = slot.ensure('en', load);
    expect(slot.loading, isTrue);
    expect(slot.voiceFor('en'), isNull);
    c.complete('EN');
    await Future.wait([a, b]);
    expect(loads, 1);
    expect(slot.voiceFor('en'), 'EN');
    expect(slot.loading, isFalse);
    await slot.ensure('en', load); // already loaded
    expect(loads, 1);
    expect(disposed, isEmpty);
  });

  test('a voice that finishes loading after the language changed is disposed', () async {
    final en = Completer<String?>();
    final ru = Completer<String?>();
    final a = slot.ensure('en', () => en.future);
    final b = slot.ensure('ru', () => ru.future);
    // The new language finishes first, the old one afterwards (the order that used to leave
    // the English voice reading Russian text).
    ru.complete('RU');
    await b;
    en.complete('EN');
    await a;
    expect(slot.voiceFor('ru'), 'RU');
    expect(slot.voiceFor('en'), isNull);
    expect(disposed, ['EN']);
    expect(slot.loading, isFalse);
  });

  test('changing the language disposes the loaded voice', () async {
    await slot.ensure('en', () async => 'EN');
    await slot.ensure('ru', () async => 'RU');
    expect(disposed, ['EN']);
    expect(slot.voiceFor('en'), isNull);
    expect(slot.voiceFor('ru'), 'RU');
    expect(slot.lang, 'ru');
  });

  test('clear disposes the voice, and a load in flight when it arrives', () async {
    await slot.ensure('en', () async => 'EN');
    slot.clear();
    expect(disposed, ['EN']);
    expect(slot.voiceFor('en'), isNull);
    expect(slot.lang, isNull);

    final c = Completer<String?>();
    final f = slot.ensure('ru', () => c.future);
    slot.clear(); // 對話翻譯 turned off while the voice loads
    expect(slot.loading, isFalse);
    c.complete('RU');
    await f;
    expect(disposed, ['EN', 'RU']);
    expect(slot.voiceFor('ru'), isNull);
  });

  test('a voice that is not installed is looked for again next time', () async {
    var installed = false;
    Future<String?> load() async => installed ? 'EN' : null;
    await slot.ensure('en', load);
    expect(slot.voiceFor('en'), isNull);
    installed = true; // the pack was imported meanwhile
    await slot.ensure('en', load);
    expect(slot.voiceFor('en'), 'EN');
    expect(disposed, isEmpty);
  });

  test('a failed load reports the error and can be retried', () async {
    await expectLater(slot.ensure('en', () async => throw StateError('init failed')),
        throwsStateError);
    expect(slot.loading, isFalse);
    expect(slot.voiceFor('en'), isNull);
    await slot.ensure('en', () async => 'EN');
    expect(slot.voiceFor('en'), 'EN');
  });

  test('a stale failure does not disturb the current voice', () async {
    final en = Completer<String?>();
    final a = slot.ensure('en', () => en.future);
    await slot.ensure('ru', () async => 'RU');
    en.completeError(StateError('old load failed'));
    await expectLater(a, throwsStateError);
    expect(slot.voiceFor('ru'), 'RU');
    expect(slot.loading, isFalse);
    expect(disposed, isEmpty);
  });
}
