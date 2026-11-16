import 'dart:async';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import '../logic/speakers.dart';

/// Result of one finalized segment (#1155): speaker number, or [unknownSpeaker].
class SpeakerResult {
  const SpeakerResult(this.seg, this.speaker, this.ms);
  final int seg;
  final int speaker;
  final int ms;
}

/// Speaker embedding + clustering in its own isolate (#1155). Only created when 說話者標籤 is
/// on. The ASR isolate sends it `[seg, TransferableTypedData]` for every finalized segment
/// ([port]); results come back on [results]. Nothing runs on the UI isolate, and the ASR isolate
/// is never waited for.
class SpeakerWorker {
  SpeakerWorker._(this._isolate, this.port, this.results, this._from);

  final Isolate _isolate;
  final ReceivePort _from;

  /// Give this to the ASR isolate.
  final SendPort port;
  final Stream<SpeakerResult> results;

  /// Null when the model failed to load (the feature then just stays silent).
  static Future<SpeakerWorker?> spawn(String model) async {
    final from = ReceivePort();
    final iso = await Isolate.spawn(_entry, [from.sendPort, model]);
    final ctrl = StreamController<SpeakerResult>();
    final ready = Completer<SendPort?>();
    from.listen((m) {
      if (m is SendPort) {
        ready.complete(m);
      } else if (m == 'fail') {
        if (!ready.isCompleted) ready.complete(null);
      } else if (m is SpeakerResult) {
        ctrl.add(m);
      }
    });
    final p = await ready.future;
    if (p == null) {
      iso.kill(priority: Isolate.immediate);
      from.close();
      return null;
    }
    return SpeakerWorker._(iso, p, ctrl.stream, from);
  }

  void dispose() {
    port.send(null);
    _isolate.kill(priority: Isolate.beforeNextEvent);
    _from.close();
  }

  static void _entry(List<Object> args) {
    final out = args[0] as SendPort;
    final inPort = ReceivePort();
    sherpa.SpeakerEmbeddingExtractor? ex;
    try {
      sherpa.initBindings();
      ex = sherpa.SpeakerEmbeddingExtractor(
        config: sherpa.SpeakerEmbeddingExtractorConfig(
            model: args[1] as String, numThreads: 1, debug: false),
      );
    } catch (_) {
      out.send('fail');
      return;
    }
    final clusterer = SpeakerClusterer();
    out.send(inPort.sendPort);
    inPort.listen((m) {
      if (m == null) {
        ex?.free();
        inPort.close();
        return;
      }
      final msg = m as List;
      try {
        final sw = Stopwatch()..start();
        final samples = (msg[1] as TransferableTypedData).materialize().asFloat32List();
        final secs = samples.length / 16000;
        var n = unknownSpeaker;
        if (secs < clusterer.minSeconds) {
          n = clusterer.assign(const [], secs); // too short: skip the model, reuse the last
        } else {
          final st = ex!.createStream();
          try {
            st.acceptWaveform(samples: samples, sampleRate: 16000);
            st.inputFinished();
            if (ex.isReady(st)) {
              final Float32List e = ex.compute(st);
              n = clusterer.assign(e, secs);
            }
          } finally {
            st.free();
          }
        }
        out.send(SpeakerResult(msg[0] as int, n, sw.elapsedMilliseconds));
      } catch (_) {
        // a failed segment just has no label
      }
    });
  }
}
