import 'dart:async';
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

import '../logic/device_probe.dart';
import '../logic/draft_gate.dart';
import '../logic/lang_pack.dart';
import '../logic/level_gate.dart';
import '../logic/prompt.dart';
import '../logic/segmenter.dart';
import 'isolate_stop.dart';

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
  const AsrDraft(this.seg, this.text, [this.lang = 'en']);
  final int seg;
  final String text;
  final String lang;
}

/// Result of [AsrWorker.bench] (#1065): decode time of [audioMs] of synthetic audio, or
/// [busy] when speech was going on and the test was skipped.
class AsrBench extends AsrEvent {
  const AsrBench({this.decodeMs = 0, this.audioMs = 0, this.busy = false});
  final int decodeMs;
  final int audioMs;
  final bool busy;
  double get rtf => audioMs > 0 ? decodeMs / audioMs : 0;
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
      this.levelDb,
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

  /// RMS level (dBFS) of the segment audio, for the two-way loudness gate (#1479); null = unknown.
  final double? levelDb;
}

/// The extra-language recognizer (Russian, 對話翻譯) finished loading, or could not
/// ([error] set): the app keeps running on SenseVoice alone.
class AsrExtraState extends AsrEvent {
  const AsrExtraState(this.lang, {this.error});
  final String lang;
  final String? error;
  bool get ok => error == null;
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
  static const vadMaxSpeech = 6.0;

  /// Silero VAD speech probability threshold and shortest kept segment (s).
  /// Kept at 0.5 / 0.25 s on purpose (#1479): `mobile/tool/vad_replay.py` shows that
  /// raising them does not separate background fillers from real short answers. At
  /// 0.6 / 0.5 s background filler bubbles drop 30 -> 6 but 3 of 6 foreground answers
  /// ("Да.", "Нет.", "Yes.", ~0.3-0.5 s) are lost too; Silero ignores loudness, so a
  /// quiet "да" in the room scores like a close "да". Fillers are handled after ASR
  /// by `logic/filler_filter.dart` instead. Re-run the replay before changing these.
  static const vadThreshold = 0.5;
  static const vadMinSpeech = 0.25;
}

/// Runs sherpa-onnx offline ASR in a background isolate.
class AsrWorker {
  AsrWorker._(this._isolate, this._toWorker, this.events, this._fromWorker, this._ctrl);

  final Isolate _isolate;
  final ReceivePort _fromWorker;
  final StreamController<AsrEvent> _ctrl;
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
    return AsrWorker._(iso, toWorker, ctrl.stream, fromWorker, ctrl);
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

  /// Speed test (#1065): decodes 4 s of synthetic audio on the main recognizer; answers with an
  /// [AsrBench] event (busy = skipped because speech is in progress).
  void bench() => _toWorker.send('bench');

  /// Sends every finalized segment's audio to [port] (the speaker worker, #1155); null = stop.
  void setSpeakerPort(SendPort? port) => _toWorker.send(['spk', port]);

  /// 對話翻譯 with a language SenseVoice cannot hear (Russian): loads that recognizer (only now,
  /// it costs memory) and sends the segments that are not in my language to it. Null = free it
  /// and go back to SenseVoice alone. Answers with an [AsrExtraState].
  void setRoute(AsrRoute? route) => _toWorker.send(['route', route]);

  /// Turns the source-text draft (#1064) on or off at run time.
  void setDraft(bool on) => _toWorker.send(on);

  /// Frees the recognizers (and the draft isolate's) and ends the isolate, see [stopWorker].
  void dispose() {
    stopWorker(_isolate, _toWorker);
    _fromWorker.close();
    _ctrl.close();
  }

  /// Draft isolate (#1064): its own recognizer (1 thread), so a draft decode never delays the
  /// final decode. Gets [seg, TransferableTypedData] snapshots, sends [AsrDraft] to the UI port
  /// and 'done' back to the ASR isolate. null = free and exit.
  static void _draftEntry(List<Object> args) {
    final ui = args[0] as SendPort;
    final asr = args[1] as SendPort;
    final cfg = args[2] as AsrConfig;
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
      asr.send('fail'); // no draft; the final path is unaffected
      return;
    }
    final inPort = ReceivePort();
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
          final r = rec.getResult(st);
          ui.send(AsrDraft(msg[0] as int, SentenceSegmenter.normalize(r.text),
              PromptBuilder.fromSenseVoiceLang(r.lang)));
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

    (String, String) decodeSense(Float32List samples) {
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

    // 對話翻譯 with Russian: the extra recognizer exists only while a route is set.
    AsrRoute? route;
    sherpa.OfflineRecognizer? extra;

    void freeExtra() {
      extra?.free();
      extra = null;
    }

    (String, String) decodeExtra(Float32List samples) {
      final st = extra!.createStream();
      try {
        st.acceptWaveform(samples: samples, sampleRate: 16000);
        extra!.decode(st);
        return (SentenceSegmenter.normalize(extra!.getResult(st).text), route!.pack.lang);
      } finally {
        st.free();
      }
    }

    // SenseVoice listens first (it knows my language); what it does not hear as my language is
    // decoded again by the extra recognizer. Manual side button: no guessing, no wasted decode.
    (String, String) decode(Float32List samples) {
      final r = route;
      if (r == null || extra == null || r.force == false) return decodeSense(samples);
      try {
        if (r.force == true) return decodeExtra(samples);
        final s = decodeSense(samples);
        return r.useExtra(s.$2) ? decodeExtra(samples) : s;
      } catch (e) {
        // A broken extra model must not stop the app: SenseVoice alone from here on.
        freeExtra();
        out.send(AsrExtraState(r.pack.lang, error: '$e'));
        return decodeSense(samples);
      }
    }

    void setRoute(AsrRoute? r) {
      // Same recognizer already loaded (the side button only changes the routing): keep it.
      if (r != null && extra != null && route?.pack.encoder == r.pack.encoder) {
        route = r;
        return;
      }
      freeExtra();
      route = null;
      if (r == null) return;
      try {
        extra = sherpa.OfflineRecognizer(
          sherpa.OfflineRecognizerConfig(
            model: sherpa.OfflineModelConfig(
              transducer: sherpa.OfflineTransducerModelConfig(
                encoder: r.pack.encoder,
                decoder: r.pack.decoder,
                joiner: r.pack.joiner,
              ),
              tokens: r.pack.tokens,
              numThreads: cfg.numThreads,
              provider: 'cpu',
              modelType: 'transducer',
              debug: false,
            ),
          ),
        );
        route = r;
        out.send(AsrExtraState(r.pack.lang));
      } catch (e) {
        out.send(AsrExtraState(r.pack.lang, error: '$e'));
      }
    }

    SendPort? spk; // speaker worker (#1155); null = labels off, nothing extra is done

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
            levelDb: seg >= 0 ? LevelGate.rmsDb(samples) : null,
            lang: lang));
        // After the final went out, so the subtitle path never waits for this.
        if (spk != null && seg >= 0) {
          spk!.send([seg, TransferableTypedData.fromList([samples])]);
        }
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
            threshold: AsrConfig.vadThreshold,
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
    final helper = LazyHelper(); // one draft isolate at a time, stopped even while it loads
    draftIn.listen((m) {
      if (m is SendPort || m == 'fail') {
        final last = helper.arrived();
        if (m is SendPort) {
          if (draftOn && !last) {
            draftPort = m;
          } else {
            m.send(null); // switched off (or the worker stopped) while it was loading
          }
        }
        if (last) draftIn.close();
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
        if (helper.close()) draftIn.close();
        vad?.free();
        rec?.free();
        freeExtra();
        inPort.close();
        return;
      }
      if (m == 'bench') {
        if (speaking) {
          out.send(const AsrBench(busy: true));
          return;
        }
        try {
          final a = benchAudio();
          var best = 1 << 30;
          for (var i = 0; i < 2; i++) {
            // first run warms up; keep the faster one
            final sw = Stopwatch()..start();
            decodeSense(a);
            best = best < sw.elapsedMilliseconds ? best : sw.elapsedMilliseconds;
          }
          out.send(AsrBench(decodeMs: best, audioMs: a.length * 1000 ~/ 16000));
        } catch (e) {
          out.send(AsrError('ASR bench failed: $e'));
        }
        return;
      }
      if (m is List && m.isNotEmpty && m[0] == 'route') {
        setRoute(m[1] as AsrRoute?);
        return;
      }
      if (m is List && m.isNotEmpty && m[0] == 'spk') {
        spk = m[1] as SendPort?; // SendPort = on, null = off
        return;
      }
      if (m is bool) {
        if (m == draftOn) return;
        draftOn = m;
        if (m) {
          // Spawned lazily: a second recognizer costs memory, only paid when the draft is on.
          // Off and on again while it loads: the one already loading is used, not a second one.
          if (helper.shouldSpawn()) Isolate.spawn(_draftEntry, [out, draftIn.sendPort, cfg]);
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
