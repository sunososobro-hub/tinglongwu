import 'dart:async';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import '../logic/prompt.dart';
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
  const AsrFinal(this.text,
      {required this.forced,
      this.decodeMs = 0,
      this.audioMs = 0,
      this.lang = 'en'});
  final String text;

  /// Detected source language code (`zh`/`en`/`ja`/`ko`/`yue`), for the MT prompt.
  final String lang;

  /// True when the VAD cut the segment because it hit the max length.
  final bool forced;

  /// Wall time of the offline decode, and the audio length it covered.
  final int decodeMs;
  final int audioMs;
}

class AsrError extends AsrEvent {
  const AsrError(this.message);
  final String message;
}

class AsrConfig {
  const AsrConfig({
    required this.model,
    required this.tokens,
    required this.vadModel,
    this.testWavDir,
    this.numThreads = 4,
  });

  final String model, tokens, vadModel;

  /// Debug: if this dir exists, every *.wav in it is decoded at startup and
  /// emitted as a final (to exercise ASR + translation without a mic).
  final String? testWavDir;
  final int numThreads;

  /// VAD rules (seconds). Speech end is detected after [vadMinSilence].
  static const vadMinSilence = 0.6;
  static const vadMinSpeech = 0.25;
  static const vadMaxSpeech = 10.0;
}

/// Runs sherpa-onnx offline ASR in a background isolate.
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
    sherpa.OfflineRecognizer? rec;
    sherpa.VoiceActivityDetector? vad;
    out.send(inPort.sendPort);

    (String, String) decode(Float32List samples) {
      final st = rec!.createStream();
      try {
        st.acceptWaveform(samples: samples, sampleRate: 16000);
        rec.decode(st);
        final r = rec.getResult(st);
        return (SentenceSegmenter.normalize(r.text), PromptBuilder.fromSenseVoiceLang(r.lang));
      } finally {
        st.free();
      }
    }

    void emit(Float32List samples, {required bool forced}) {
      final sw = Stopwatch()..start();
      final (text, lang) = decode(samples);
      final ms = sw.elapsedMilliseconds;
      if (text.isNotEmpty) {
        out.send(AsrFinal(text,
            forced: forced,
            decodeMs: ms,
            audioMs: samples.length * 1000 ~/ 16000,
            lang: lang));
      }
    }

    try {
      sherpa.initBindings();
      rec = sherpa.OfflineRecognizer(
        sherpa.OfflineRecognizerConfig(
          model: sherpa.OfflineModelConfig(
            senseVoice: sherpa.OfflineSenseVoiceModelConfig(
              model: cfg.model,
              language: '', // auto-detect
              useInverseTextNormalization: true,
            ),
            tokens: cfg.tokens,
            numThreads: cfg.numThreads,
            provider: 'cpu',
            modelType: 'sense_voice',
            debug: false,
          ),
        ),
      );
      vad = sherpa.VoiceActivityDetector(
        config: sherpa.VadModelConfig(
          sileroVad: sherpa.SileroVadModelConfig(
            model: cfg.vadModel,
            threshold: 0.5,
            minSilenceDuration: AsrConfig.vadMinSilence,
            minSpeechDuration: AsrConfig.vadMinSpeech,
            maxSpeechDuration: AsrConfig.vadMaxSpeech,
          ),
          sampleRate: 16000,
          numThreads: 1,
          debug: false,
        ),
        bufferSizeInSeconds: 30,
      );
    } catch (e) {
      out.send(AsrError('ASR init failed: $e'));
      return;
    }
    out.send(const AsrReady());

    // Debug wav files (decoded whole, no VAD).
    final td = cfg.testWavDir;
    if (td != null && Directory(td).existsSync()) {
      final files = Directory(td)
          .listSync()
          .whereType<File>()
          .where((f) => f.path.toLowerCase().endsWith('.wav'))
          .toList()
        ..sort((a, b) => a.path.compareTo(b.path));
      for (final f in files) {
        try {
          final w = sherpa.readWave(f.path);
          if (w.samples.isEmpty) continue;
          emit(w.samples, forced: false);
        } catch (e) {
          out.send(AsrError('test wav ${f.path}: $e'));
        }
      }
    }

    var speaking = false;
    inPort.listen((m) {
      if (m == null) {
        vad?.free();
        rec?.free();
        inPort.close();
        return;
      }
      try {
        final samples =
            (m as TransferableTypedData).materialize().asFloat32List();
        vad!.acceptWaveform(samples);
        final det = vad.isDetected();
        if (det != speaking) {
          speaking = det;
          out.send(AsrPartial(det ? '…' : ''));
        }
        while (!vad.isEmpty()) {
          final seg = vad.front();
          vad.pop();
          final dur = seg.samples.length / 16000;
          emit(seg.samples, forced: dur >= AsrConfig.vadMaxSpeech - 0.3);
        }
      } catch (e) {
        out.send(AsrError('ASR runtime error: $e'));
      }
    });
  }
}
