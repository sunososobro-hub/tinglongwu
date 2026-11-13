import 'dart:async';
import 'dart:collection';

import 'package:flutter/foundation.dart';
import 'package:record/record.dart';

import '../logic/context_budget.dart';
import '../logic/subtitle_trim.dart';
import 'app_log.dart';
import 'asr_worker.dart';
import 'model_paths.dart';
import 'translator.dart';

enum Stage { starting, loadingModels, listening, missingModels, error }

class Subtitle {
  Subtitle(this.ru, {this.zh, this.traditionalOk = true});
  String ru;
  String? zh;
  bool traditionalOk;
}

class _Job {
  _Job(this.ru, this.sub, this.speechEndMs, this.asrFinalMs, [this.lang = 'en']);
  final String lang;
  String ru;
  final Subtitle sub;

  /// Estimated time the speaker stopped (endpoint time minus trailing-silence rule).
  final int speechEndMs;
  int asrFinalMs;
}

/// mic -> sherpa ASR isolate -> queue -> Hy-MT2 translator -> subtitles.
class Pipeline extends ChangeNotifier {
  Stage stage = Stage.starting;
  String? errorText;
  List<String> missingFiles = const [];
  ModelPaths? paths;

  String draftRu = '';
  final List<Subtitle> subtitles = []; // newest last
  int queued = 0;

  // Debug stats.
  int? lastLatencyMs; // estimated speech end -> zh shown
  int? lastAfterFinalMs; // ASR final -> zh shown
  double? lastTps;
  int? lastTokens;
  int? lastFirstTokenMs;
  int dropped = 0; // number of merges done to keep the queue short
  String? logPath;

  /// Max waiting jobs; beyond this the two oldest are merged into one.
  static const maxQueue = 2;
  static const keepSubtitles = 4;

  final Stopwatch _clock = Stopwatch()..start();
  final AudioRecorder _rec = AudioRecorder();
  AsrWorker? _asr;
  Translator? _tr;
  StreamSubscription<Uint8List>? _micSub;
  StreamSubscription<AsrEvent>? _asrSub;
  final Queue<_Job> _queue = Queue();
  bool _busy = false;
  bool _disposed = false;

  AppLog get _log => AppLog.instance;

  Future<void> start() async {
    try {
      paths = await ModelPaths.resolve();
      await AppLog.init(paths!.filesDir);
      logPath = _log.path;
      _log.i('files dir ${paths!.filesDir}');
      final missing = paths!.missing();
      if (missing.isNotEmpty) {
        missingFiles = missing;
        _log.e('models missing: $missing');
        stage = Stage.missingModels;
        notifyListeners();
        return;
      }

      stage = Stage.loadingModels;
      notifyListeners();

      if (!await _rec.hasPermission()) {
        _fail('Microphone permission denied. Grant it in system settings and restart.');
        return;
      }

      final p = paths!;
      final t0 = _clock.elapsedMilliseconds;
      _asr = await AsrWorker.spawn(AsrConfig(
        model: p.asrFile('model.int8.onnx'),
        tokens: p.asrFile('tokens.txt'),
        vadModel: p.vadPath,
        testWavDir: p.testWavDir,
      ));
      final ready = Completer<void>();
      _asrSub = _asr!.events.listen((e) {
        switch (e) {
          case AsrReady():
            if (!ready.isCompleted) ready.complete();
          case AsrError(:final message):
            _log.e(message);
            if (!ready.isCompleted) {
              ready.completeError(message);
            } else {
              _fail(message);
            }
          case AsrPartial(:final text):
            draftRu = text;
            notifyListeners();
          case AsrFinal():
            _onFinal(e);
        }
      });
      await ready.future;
      _log.i('ASR ready in ${_clock.elapsedMilliseconds - t0} ms');

      final t1 = _clock.elapsedMilliseconds;
      final tr = await Translator.load(p.llmPath);
      if (_disposed) {
        tr.dispose(); // replaced (e.g. by Import models) while loading
        return;
      }
      _tr = tr;
      _log.i('LLM ready in ${_clock.elapsedMilliseconds - t1} ms (${p.llmPath.split('/').last}) '
          '${await ModelPaths.statsLine()}');

      _drain(); // anything queued while the LLM was loading (debug wavs)
      if (_disposed) return;
      final stream = await _rec.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: 16000,
        numChannels: 1,
      ));
      _micSub = stream.listen(_asr!.feedPcm16,
          onError: (Object e) => _fail('Mic error: $e'));
      stage = Stage.listening;
      _log.i('listening');
      notifyListeners();
    } catch (e, st) {
      _log.e('start failed: $e\n$st');
      _fail('$e');
    }
  }

  void _fail(String msg) {
    errorText = msg;
    stage = Stage.error;
    try {
      _log.e('FAIL: $msg');
    } catch (_) {}
    notifyListeners();
  }

  /// Keep the last [keepSubtitles] lines, but never drop a line whose
  /// translation is still queued or in flight: the queue can now grow past
  /// [maxQueue] when no merge fits the context (#533).
  void _trimSubtitles() =>
      trimFinished<Subtitle>(subtitles, keepSubtitles, (s) => s.zh != null);

  void _onFinal(AsrFinal f) {
    final now = _clock.elapsedMilliseconds;
    // VAD emits the segment after minSilence of trailing silence + decode.
    final speechEnd = f.forced
        ? now - f.decodeMs
        : now - f.decodeMs - (AsrConfig.vadMinSilence * 1000).round();
    _log.i('ASR final${f.forced ? " (forced)" : ""} '
        'lang=${f.lang} decode=${f.decodeMs}ms audio=${f.audioMs}ms: ${AppLog.text(f.text)}');
    final sub = Subtitle(f.text);
    subtitles.add(sub);
    _trimSubtitles();
    _queue.add(_Job(f.text, sub, speechEnd, now, f.lang));
    // Bound the backlog: merge the oldest two waiting jobs (keeps all text,
    // costs one fewer LLM call) instead of dropping. A merge that would be
    // longer than ContextBudget.maxMergeChars is skipped (#533): it could
    // overflow the LLM context and stop the app.
    while (_queue.length > maxQueue) {
      final jobs = _queue.toList();
      final i = ContextBudget.mergeIndex([for (final j in jobs) j.ru]);
      if (i == null) {
        _log.i('queue>$maxQueue, no merge fits ${ContextBudget.maxMergeChars} chars');
        break;
      }
      final a = jobs[i];
      final b = jobs[i + 1];
      b.ru = ContextBudget.join(a.ru, b.ru);
      b.sub.ru = b.ru;
      subtitles.remove(a.sub);
      final merged = _Job(b.ru, b.sub, a.speechEndMs, b.asrFinalMs, b.lang);
      jobs
        ..[i] = merged
        ..removeAt(i + 1);
      _queue
        ..clear()
        ..addAll(jobs);
      dropped++;
      _log.i('queue>$maxQueue, merged jobs $i+${i + 1} -> ${AppLog.text(merged.ru)}');
    }
    queued = _queue.length + (_busy ? 1 : 0);
    notifyListeners();
    _drain();
  }

  Future<void> _drain() async {
    if (_busy || _disposed || _tr == null) return;
    _busy = true;
    try {
      while (_queue.isNotEmpty && !_disposed) {
        final job = _queue.removeFirst();
        queued = _queue.length + 1;
        notifyListeners();
        try {
          final r = await _tr!.translate(job.ru, src: job.lang);
          final now = _clock.elapsedMilliseconds;
          lastLatencyMs = now - job.speechEndMs;
          lastAfterFinalMs = now - job.asrFinalMs;
          lastTps = r.tokensPerSecond;
          lastTokens = r.tokens;
          lastFirstTokenMs = r.firstTokenMs;
          job.sub.zh = r.zh;
          _trimSubtitles();
          job.sub.traditionalOk = r.check.isTraditional;
          _log.i('ZH [${r.check.isTraditional ? "zh-Hant" : "NOT-TRAD ${r.check}"}'
              '] ${AppLog.text(r.zh)} | '
              'lat=${lastLatencyMs}ms afterFinal=${lastAfterFinalMs}ms '
              'ttft=${r.firstTokenMs}ms tok=${r.tokens} '
              'tps=${r.tokensPerSecond.toStringAsFixed(1)} n_ctx=${r.nCtx} '
              'queue=${_queue.length} ${await ModelPaths.statsLine()}');
        } catch (e, st) {
          _log.e('translate failed: $e\n$st');
          _fail('Translation failed: $e');
          return;
        }
        notifyListeners();
      }
    } finally {
      _busy = false;
      queued = _queue.length;
      if (!_disposed) notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _micSub?.cancel();
    _asrSub?.cancel();
    _rec.dispose();
    _asr?.dispose();
    _tr?.dispose();
    try {
      _log.i('=== session end ===');
      _log.flush();
    } catch (_) {}
    super.dispose();
  }
}
