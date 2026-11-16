import 'dart:async';
import 'dart:isolate';

/// Stops a worker isolate without leaking its native models (#1272).
///
/// The workers (ASR, draft, TTS, speaker) own sherpa-onnx objects that have no finalizer: only
/// the worker's own `null` handler frees them. So the worker is asked to stop ([toWorker] gets
/// `null`), frees what it holds, closes its ports and ends by itself. Killing it right away (the
/// old `kill(beforeNextEvent)`) ended the isolate before that handler ran and left hundreds of MB
/// allocated on every restart.
///
/// A worker that has not ended after [grace] (stuck in a long decode, or broken) is killed.
/// The future completes when the isolate is gone: true = it ended by itself.
Future<bool> stopWorker(Isolate isolate, SendPort toWorker,
    {Duration grace = const Duration(seconds: 10)}) {
  final done = Completer<bool>();
  final exit = RawReceivePort();
  Timer? timer;
  void finish(bool clean) {
    if (done.isCompleted) return;
    timer?.cancel();
    exit.close();
    done.complete(clean);
  }

  exit.handler = (_) => finish(true);
  isolate.addOnExitListener(exit.sendPort);
  timer = Timer(grace, () {
    isolate.kill(priority: Isolate.immediate);
    finish(false);
  });
  toWorker.send(null);
  return done.future;
}

/// Bookkeeping for the one helper isolate a worker spawns lazily (the draft recognizer): never
/// two at a time, and a shutdown that arrives while it is still loading waits for it, so it can
/// be told to stop (pure, see the tests).
class LazyHelper {
  bool _loading = false;
  bool _closing = false;

  bool get loading => _loading;
  bool get closing => _closing;

  /// The feature was turned on: true = spawn the helper now (false: one is already loading).
  bool shouldSpawn() {
    if (_loading || _closing) return false;
    return _loading = true;
  }

  /// The helper reported in (its port arrived) or failed to load. Returns whether the owner may
  /// close the port it listens to the helper on (it is shutting down).
  bool arrived() {
    _loading = false;
    return _closing;
  }

  /// The owner shuts down. Returns whether it may close its helper port now (false: a helper is
  /// still loading, close on [arrived]).
  bool close() {
    _closing = true;
    return !_loading;
  }
}
