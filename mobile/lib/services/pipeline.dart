import 'dart:async';
import 'dart:collection';

import 'package:flutter/foundation.dart';
import 'package:record/record.dart';

import 'app_log.dart';
import 'asr_worker.dart';
import 'model_paths.dart';
import 'translator.dart';

enum Stage { starting, loadingModels, listening, missingModels, error }

class Subtitle {
  Subtitle(this.ru, {this.zh, this.traditionalOk = true});
  final String ru;
  String? zh;
  bool traditionalOk;
}

class _Job {
  _Job(this.ru, this.speechEndMs, this.asrFinalMs);
  final String ru;

  /// Estimated time the speaker stopped (endpoint time minus trailing-silence rule).
  final int speechEndMs;
  final int asrFinalMs;
}

/// mic -> sherpa ASR isolate -> queue -> Qwen3 translator -> subtitles.
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
  int dropped = 0;
  String? logPath;

  static const maxQueue = 3;
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
        encoder: p.asrFile('encoder.int8.onnx'),
        decoder: p.asrFile('decoder.onnx'),
        joiner: p.asrFile('joiner.int8.onnx'),
        tokens: p.asrFile('tokens.txt'),
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
      _tr = await Translator.load(p.llmPath);
      _log.i('LLM ready in ${_clock.elapsedMilliseconds - t1} ms');

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

  void _onFinal(AsrFinal f) {
    final now = _clock.elapsedMilliseconds;
    final speechEnd =
        f.forced ? now : now - (AsrConfig.rule2 * 1000).round();
    _log.i('ASR final${f.forced ? " (forced)" : ""}: ${f.text}');
    subtitles.add(Subtitle(f.text));
    if (subtitles.length > keepSubtitles) subtitles.removeAt(0);
    if (_queue.length >= maxQueue) {
      final old = _queue.removeFirst();
      dropped++;
      _log.e('queue full, dropped untranslated: ${old.ru}');
    }
    _queue.add(_Job(f.text, speechEnd, now));
    queued = _queue.length + (_busy ? 1 : 0);
    notifyListeners();
    _drain();
  }

  Future<void> _drain() async {
    if (_busy || _disposed) return;
    _busy = true;
    try {
      while (_queue.isNotEmpty && !_disposed) {
        final job = _queue.removeFirst();
        queued = _queue.length + 1;
        notifyListeners();
        try {
          final r = await _tr!.translate(job.ru);
          final now = _clock.elapsedMilliseconds;
          lastLatencyMs = now - job.speechEndMs;
          lastAfterFinalMs = now - job.asrFinalMs;
          lastTps = r.tokensPerSecond;
          lastTokens = r.tokens;
          lastFirstTokenMs = r.firstTokenMs;
          for (final s in subtitles) {
            if (s.ru == job.ru && s.zh == null) {
              s.zh = r.zh;
              s.traditionalOk = r.check.isTraditional;
              break;
            }
          }
          _log.i('ZH [${r.check.isTraditional ? "zh-Hant" : "NOT-TRAD ${r.check}"}'
              '${r.retried ? " retried" : ""}] ${r.zh} | '
              'lat=${lastLatencyMs}ms afterFinal=${lastAfterFinalMs}ms '
              'ttft=${r.firstTokenMs}ms tok=${r.tokens} '
              'tps=${r.tokensPerSecond.toStringAsFixed(1)} queue=${_queue.length}');
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
