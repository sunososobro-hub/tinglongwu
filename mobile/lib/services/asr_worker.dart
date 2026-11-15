import 'dart:async';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import '../logic/draft_gate.dart';
import '../logic/prompt.dart';
import '../logic/segmenter.dart';

/// Messages from the ASR isolate to the UI isolate.
sealed class AsrEvent {
  const AsrEvent();
}

class AsrReady extends AsrEvent {
  const AsrReady();
}

/// Speech started / stopped according to the VAD (drives the draft hold, #1064).
class AsrSpeech extends AsrEvent {
  const AsrSpeech(this.speaking);
  final bool speaking;
}

/// Interim source text of the segment [seg] still being spoken (#1064). Decoded in a separate
/// isolate, so it can arrive after that segment's final: the receiver drops it then.
class AsrDraft extends AsrEvent {
  const AsrDraft(this.seg, this.text);
  final int seg;
  final String text;
}

/// A VAD segment ended without any text (nothing recognized): clears its draft.
class AsrSegmentEnd extends AsrEvent {
  const AsrSegmentEnd(this.seg);
  final int seg;
}

class AsrFinal extends AsrEvent {
  const AsrFinal(this.text,
      {required this.forced,
      this.seg = -1,
      this.decodeMs = 0,
      this.audioMs = 0,
      this.lang = 'en'});
  final String text;

  /// Detected source language code (`zh`/`en`/`ja`/`ko`/`yue`), for the MT prompt.
  final String lang;

  /// Index of the VAD segment this final belongs to (see [AsrDraft]).
  final int seg;

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

  /// VAD rules (seconds). Speech end is detected after [vadMinSilence]. Video speech has few
  /// long pauses, so the VAD hard cap [vadMaxSpeech] decides the subtitle rate: 6 s keeps one
  /// subtitle to roughly one sentence and updates at least every ~6 s (was 10 s + 3-4 sentences).
  static const vadMinSilence = 0.35;
  static const vadMinSpeech = 0.25;
  static const vadMaxSpeech = 6.0;
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

  /// Turns the source-text draft (#1064) on or off at run time.
  void setDraft(bool on) => _toWorker.send(on);

  void dispose() {
    _toWorker.send(null);
    _isolate.kill(priority: Isolate.beforeNextEvent);
  }

  /// Draft isolate (#1064): its own recognizer (1 thread), so a draft decode never delays the
  /// final decode. Gets [seg, TransferableTypedData] snapshots, sends [AsrDraft] to the UI port
  /// and 'done' back to the ASR isolate. null = free and exit.
  static void _draftEntry(List<Object> args) {
    final ui = args[0] as SendPort;
    final asr = args[1] as SendPort;
    final cfg = args[2] as AsrConfig;
    final inPort = ReceivePort();
    sherpa.OfflineRecognizer? rec;
    try {
      sherpa.initBindings();
      rec = sherpa.OfflineRecognizer(
        sherpa.OfflineRecognizerConfig(
          model: sherpa.OfflineModelConfig(
            senseVoice: sherpa.OfflineSenseVoiceModelConfig(
              model: cfg.model,
              language: '',
              useInverseTextNormalization: true,
            ),
            tokens: cfg.tokens,
            numThreads: 1,
            provider: 'cpu',
            modelType: 'sense_voice',
            debug: false,
          ),
        ),
      );
    } catch (_) {
      return; // no draft; the final path is unaffected
    }
    asr.send(inPort.sendPort);
    inPort.listen((m) {
      if (m == null) {
        rec?.free();
        inPort.close();
        return;
      }
      final msg = m as List;
      try {
        final samples = (msg[1] as TransferableTypedData).materialize().asFloat32List();
        final st = rec!.createStream();
        try {
          st.acceptWaveform(samples: samples, sampleRate: 16000);
          rec.decode(st);
          final text = SentenceSegmenter.normalize(rec.getResult(st).text);
          ui.send(AsrDraft(msg[0] as int, text));
        } finally {
          st.free();
        }
      } catch (_) {
        // a failed draft is just skipped
      }
      asr.send('done');
    });
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

    void emit(Float32List samples, {required bool forced, int seg = -1}) {
      final sw = Stopwatch()..start();
      final (text, lang) = decode(samples);
      final ms = sw.elapsedMilliseconds;
      if (text.isNotEmpty) {
        out.send(AsrFinal(text,
            forced: forced,
            seg: seg,
            decodeMs: ms,
            audioMs: samples.length * 1000 ~/ 16000,
            lang: lang));
      } else {
        out.send(AsrSegmentEnd(seg));
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
    var seg = 0; // VAD segments popped so far
    final clock = Stopwatch()..start();
    final gate = DraftGate();
    final audio = DraftAudio();
    final draftIn = ReceivePort();
    SendPort? draftPort;
    var draftOn = false;
    draftIn.listen((m) {
      if (m is SendPort) {
        if (draftOn) {
          draftPort = m;
        } else {
          m.send(null); // switched off while it was loading
        }
      } else if (m == 'done') {
        gate.finished();
      }
    });
    void stopDraft() {
      draftPort?.send(null);
      draftPort = null;
      gate.finished();
    }

    inPort.listen((m) {
      if (m == null) {
        stopDraft();
        draftIn.close();
        vad?.free();
        rec?.free();
        inPort.close();
        return;
      }
      if (m is bool) {
        if (m == draftOn) return;
        draftOn = m;
        if (m) {
          // Spawned lazily: a second recognizer costs memory, only paid when the draft is on.
          Isolate.spawn(_draftEntry, [out, draftIn.sendPort, cfg]);
        } else {
          stopDraft();
        }
        return;
      }
      try {
        final samples =
            (m as TransferableTypedData).materialize().asFloat32List();
        vad!.acceptWaveform(samples);
        final det = vad.isDetected();
        if (det != speaking) {
          speaking = det;
          out.send(AsrSpeech(det));
        }
        if (draftOn) audio.feed(samples, detected: det);
        while (!vad.isEmpty()) {
          final s = vad.front();
          vad.pop();
          final dur = s.samples.length / 16000;
          emit(s.samples, forced: dur >= AsrConfig.vadMaxSpeech - 0.3, seg: seg);
          seg++;
          audio.segmentDone();
          gate.segmentReset();
        }
        // Draft: re-recognize the segment in progress, throttled; never blocks the final path.
        final port = draftPort;
        if (draftOn && port != null && det && audio.active) {
          final now = clock.elapsedMilliseconds;
          if (gate.shouldRun(nowMs: now, samples: audio.length)) {
            final snap = audio.snapshot();
            gate.started(nowMs: now, samples: audio.length);
            port.send([seg, TransferableTypedData.fromList([snap])]);
          }
        }
      } catch (e) {
        out.send(AsrError('ASR runtime error: $e'));
      }
    });
  }
}
