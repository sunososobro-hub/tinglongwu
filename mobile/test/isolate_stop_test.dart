import 'dart:async';
import 'dart:isolate';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/isolate_stop.dart';

/// A worker shaped like the real ones: on `null` it "frees its model" (tells [args] so) and
/// closes its port, which ends the isolate.
void _polite(List<Object> args) {
  final out = args[0] as SendPort;
  final inPort = ReceivePort();
  out.send(inPort.sendPort);
  inPort.listen((m) {
    if (m == null) {
      out.send('freed');
      inPort.close();
      return;
    }
    out.send('got $m');
  });
}

/// A worker that never stops by itself.
void _deaf(List<Object> args) {
  final inPort = ReceivePort();
  (args[0] as SendPort).send(inPort.sendPort);
  inPort.listen((_) {});
}

Future<(Isolate, SendPort, List<Object?>, ReceivePort)> _spawn(
    void Function(List<Object>) entry) async {
  final from = ReceivePort();
  final seen = <Object?>[];
  final port = Completer<SendPort>();
  from.listen((m) => m is SendPort ? port.complete(m) : seen.add(m));
  final iso = await Isolate.spawn(entry, [from.sendPort]);
  return (iso, await port.future, seen, from);
}

void main() {
  test('a stopped worker frees its model before the isolate ends', () async {
    final (iso, port, seen, from) = await _spawn(_polite);
    port.send('work'); // queued before the stop: still handled, then the cleanup
    final clean = await stopWorker(iso, port);
    await Future<void>.delayed(const Duration(milliseconds: 50));
    expect(clean, isTrue);
    expect(seen, ['got work', 'freed']);
    from.close();
  });

  test('a worker that does not stop is killed after the grace period', () async {
    final (iso, port, _, from) = await _spawn(_deaf);
    final gone = ReceivePort();
    iso.addOnExitListener(gone.sendPort, response: 'gone');
    final sw = Stopwatch()..start();
    final clean = await stopWorker(iso, port, grace: const Duration(milliseconds: 200));
    expect(clean, isFalse);
    expect(sw.elapsedMilliseconds, greaterThanOrEqualTo(190));
    expect(await gone.first.timeout(const Duration(seconds: 5)), 'gone');
    from.close();
  });

  group('LazyHelper (draft isolate bookkeeping)', () {
    test('off and on again while loading does not spawn a second helper', () {
      final h = LazyHelper();
      expect(h.shouldSpawn(), isTrue);
      expect(h.shouldSpawn(), isFalse);
      expect(h.arrived(), isFalse); // running: the owner keeps listening
      expect(h.shouldSpawn(), isTrue); // stopped and turned on again later: a new one
    });

    test('shutdown waits for a helper that is still loading', () {
      final h = LazyHelper();
      h.shouldSpawn();
      expect(h.close(), isFalse); // keep the port: the helper must still be told to stop
      expect(h.shouldSpawn(), isFalse);
      expect(h.arrived(), isTrue); // now close it
    });

    test('shutdown with no helper loading closes at once', () {
      final h = LazyHelper();
      expect(h.close(), isTrue);
      h.shouldSpawn();
      expect(h.loading, isFalse);
    });
  });
}
