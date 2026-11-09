import 'dart:async';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import '../logic/segmenter.dart';

/// Messages from the ASR isolate to the UI isolate.
sealed class AsrEvent {
  const AsrEvent();
}

class AsrReady extends AsrEvent {
  const AsrReady();
}

class AsrPartial extends AsrEvent {
  const AsrPartial(this.text);
  final String text;
}

class AsrFinal extends AsrEvent {
  const AsrFinal(this.text, {required this.forced});
  final String text;

  /// True when committed because the utterance got too long (no endpoint).
  final bool forced;
}

class AsrError extends AsrEvent {
  const AsrError(this.message);
  final String message;
}

class AsrConfig {
  const AsrConfig({
    required this.encoder,
    required this.decoder,
    required this.joiner,
    required this.tokens,
    this.numThreads = 2,
    this.maxWords = 24,
  });

  final String encoder, decoder, joiner, tokens;
  final int numThreads;
  final int maxWords;

  /// Endpoint rules (seconds of trailing silence). Keep in sync with the
  /// latency estimate in the pipeline.
  static const rule1 = 1.6; // silence, nothing decoded yet
  static const rule2 = 0.9; // silence after speech -> endpoint
  static const rule3 = 14.0; // max utterance length
}

/// Runs sherpa-onnx streaming ASR in a background isolate.
class AsrWorker {
  AsrWorker._(this._isolate, this._toWorker, this.events);

  final Isolate _isolate;
  final SendPort _toWorker;
  final Stream<AsrEvent> events;

  static Future<AsrWorker> spawn(AsrConfig cfg) async {
    final fromWorker = ReceivePort();
    final iso = await Isolate.spawn(_entry, [fromWorker.sendPort, cfg]);
    final ctrl = StreamController<AsrEvent>(); // single-subscription: buffers until listened
    final portCompleter = Completer<SendPort>();
    fromWorker.listen((m) {
      if (m is SendPort) {
        portCompleter.complete(m);
      } else if (m is AsrEvent) {
        ctrl.add(m);
      }
    });
    final toWorker = await portCompleter.future;
    return AsrWorker._(iso, toWorker, ctrl.stream);
  }

  /// Feed 16 kHz mono PCM16 little-endian bytes.
  void feedPcm16(Uint8List bytes) {
    final n = bytes.lengthInBytes ~/ 2;
    if (n == 0) return;
    final bd = ByteData.sublistView(bytes);
    final f = Float32List(n);
    for (var i = 0; i < n; i++) {
      f[i] = bd.getInt16(i * 2, Endian.little) / 32768.0;
    }
    _toWorker.send(TransferableTypedData.fromList([f]));
  }

  void dispose() {
    _toWorker.send(null);
    _isolate.kill(priority: Isolate.beforeNextEvent);
  }

  static void _entry(List<Object> args) {
    final out = args[0] as SendPort;
    final cfg = args[1] as AsrConfig;
    final inPort = ReceivePort();
    sherpa.OnlineRecognizer? rec;
    sherpa.OnlineStream? stream;
    out.send(inPort.sendPort);
    try {
      sherpa.initBindings();
      rec = sherpa.OnlineRecognizer(
        sherpa.OnlineRecognizerConfig(
          model: sherpa.OnlineModelConfig(
            transducer: sherpa.OnlineTransducerModelConfig(
              encoder: cfg.encoder,
              decoder: cfg.decoder,
              joiner: cfg.joiner,
            ),
            tokens: cfg.tokens,
            numThreads: cfg.numThreads,
            provider: 'cpu',
            debug: false,
          ),
          enableEndpoint: true,
          rule1MinTrailingSilence: AsrConfig.rule1,
          rule2MinTrailingSilence: AsrConfig.rule2,
          rule3MinUtteranceLength: AsrConfig.rule3,
        ),
      );
      stream = rec.createStream();
    } catch (e) {
      out.send(AsrError('ASR init failed: $e'));
      return;
    }
    out.send(const AsrReady());

    var lastText = '';
    inPort.listen((m) {
      if (m == null) {
        stream?.free();
        rec?.free();
        inPort.close();
        return;
      }
      try {
        final samples =
            (m as TransferableTypedData).materialize().asFloat32List();
        stream!.acceptWaveform(samples: samples, sampleRate: 16000);
        while (rec!.isReady(stream)) {
          rec.decode(stream);
        }
        final text = SentenceSegmenter.normalize(rec.getResult(stream).text);
        if (text != lastText) {
          lastText = text;
          out.send(AsrPartial(text));
        }
        final endpoint = rec.isEndpoint(stream);
        final forced =
            !endpoint && SentenceSegmenter.shouldForceCommit(text, maxWords: cfg.maxWords);
        if (endpoint || forced) {
          if (text.isNotEmpty) out.send(AsrFinal(text, forced: forced));
          rec.reset(stream);
          lastText = '';
          out.send(const AsrPartial(''));
        }
      } catch (e) {
        out.send(AsrError('ASR runtime error: $e'));
      }
    });
  }
}
