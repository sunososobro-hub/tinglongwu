import 'dart:async';
import 'dart:collection';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:record/record.dart';

import '../logic/context_budget.dart';
import '../logic/phone_audio.dart';
import '../logic/subtitle_trim.dart';
import 'app_log.dart';
import 'asr_worker.dart';
import 'bundled_models.dart';
import 'model_importer.dart';
import 'model_paths.dart';
import 'playback_capture.dart';
import '../logic/target_language.dart';
import 'translator.dart';

enum Stage { starting, copyingModels, loadingModels, listening, missingModels, error }

/// Where the audio comes from: the microphone, or other apps' playback (#998, Android 10+).
enum AudioSourceKind { mic, phone }

/// Shown instead of an empty overlay when phone audio stays silent (app blocks capture).
const silenceHintText = '沒有聽到影片的聲音：這個 App 可能不允許翻譯（例如有版權保護的影片），或影片還沒開始播放';

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

/// mic or phone audio -> sherpa ASR isolate -> queue -> Hy-MT2 translator -> subtitles.
class Pipeline extends ChangeNotifier {
  Stage stage = Stage.starting;
  String? errorText;
  List<String> missingFiles = const [];
  /// 0..1 while bundled models are copied on first launch (Stage.copyingModels).
  double copyFraction = 0;
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

  /// Audio source (#998). [phoneSupported] = this device can capture other apps' audio.
  AudioSourceKind source = AudioSourceKind.mic;
  bool phoneSupported = false;
  bool switchingSource = false;

  /// True while phone audio has been silent for a while (see [silenceHintText]).
  bool silenceHint = false;
  OverlayStyle overlayStyle = const OverlayStyle();
  OverlayStyleStore? _styleStore;
  final SilenceWatch _silence = SilenceWatch();
  StreamSubscription<Uint8List>? _phoneSub;
  AppLifecycleListener? _resumeOnce;
  String? _overlaySent;

  AppLog get _log => AppLog.instance;

  /// Subtitle language code (#730): zh = zh-Hant (default), en, ...
  String target = TargetLanguage.fallback;
  TargetLanguageStore? _targetStore;

  /// Picks the subtitle language; remembered between runs.
  void setTarget(String code) {
    target = TargetLanguage.normalize(code);
    _targetStore?.save(target);
    notifyListeners();
  }

  /// First launch of a Play install with install-time packs: copy the models out of the packs.
  Future<void> _copyBundled(String modelsDir) async {
    try {
      final imp = ModelImporter(await ModelImporter.loadManifest(), modelsDir);
      final r = await BundledModels.copyMissing(imp, onProgress: (done, total, _) {
        if (stage != Stage.copyingModels) stage = Stage.copyingModels;
        copyFraction = total > 0 ? done / total : 0;
        notifyListeners();
      });
      if (r != null) _log.i('bundled models copied ${r.imported} problems ${r.problems}');
    } catch (e) {
      _log.e('bundled models copy failed: $e');
    }
  }

  Future<void> start() async {
    try {
      paths = await ModelPaths.resolve();
      await AppLog.init(paths!.filesDir);
      logPath = _log.path;
      _log.i('files dir ${paths!.filesDir}');
      _targetStore = TargetLanguageStore('${paths!.filesDir}/target_language.txt');
      target = _targetStore!.load();
      _styleStore = OverlayStyleStore('${paths!.filesDir}/overlay_style.txt');
      overlayStyle = _styleStore!.load();
      phoneSupported = await PlaybackCapture.supported();
      var missing = paths!.missing();
      if (missing.isNotEmpty) {
        await _copyBundled(paths!.modelsDir);
        missing = paths!.missing();
      }
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
      await _startMic();
      stage = Stage.listening;
      _log.i('listening');
      notifyListeners();
    } catch (e, st) {
      _log.e('start failed: $e\n$st');
      _fail('$e');
    }
  }

  Future<void> _startMic() async {
    if (_disposed || _micSub != null) return;
    final stream = await _rec.startStream(const RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1,
    ));
    if (_disposed) return;
    _micSub = stream.listen(_asr!.feedPcm16, onError: (Object e) => _fail('Mic error: $e'));
  }

  Future<void> _stopMic() async {
    final sub = _micSub;
    _micSub = null;
    await sub?.cancel();
    try {
      await _rec.stop();
    } catch (_) {}
  }

  /// Switches the audio source. Phone audio asks the system capture consent (every session, a
  /// system rule) and shows the floating overlay. Returns a short user message on failure.
  Future<String?> setSource(AudioSourceKind k) async {
    if (k == source || switchingSource || _asr == null || _disposed) return null;
    switchingSource = true;
    notifyListeners();
    try {
      if (k == AudioSourceKind.phone) {
        if (!phoneSupported) return '這支手機不支援看影片翻譯（需要 Android 10 以上）';
        final pcm = PlaybackCapture.pcm();
        _silence.reset();
        _phoneSub = pcm.listen(_onPhonePcm, onDone: _onPhoneStopped,
            onError: (Object e) {
          _log.e('phone audio error: $e');
          _onPhoneStopped();
        });
        final ok = await PlaybackCapture.start();
        if (!ok || _disposed) {
          await _phoneSub?.cancel();
          _phoneSub = null;
          _log.i('phone audio: consent declined');
          return ok ? null : '已取消看影片翻譯';
        }
        await _stopMic();
        source = AudioSourceKind.phone;
        silenceHint = false;
        _overlaySent = null;
        await PlaybackCapture.overlayStyle(overlayStyle);
        await PlaybackCapture.overlayShow();
        _log.i('source: phone audio');
      } else {
        source = AudioSourceKind.mic;
        await _phoneSub?.cancel();
        _phoneSub = null;
        await PlaybackCapture.stop();
        silenceHint = false;
        await _startMic();
        _log.i('source: mic');
      }
      return null;
    } catch (e) {
      _log.e('setSource($k) failed: $e');
      return '切換音源失敗：$e';
    } finally {
      switchingSource = false;
      if (!_disposed) notifyListeners();
    }
  }

  void _onPhonePcm(Uint8List b) {
    _asr?.feedPcm16(b);
    final s = _silence.feed(b);
    if (s != silenceHint) {
      silenceHint = s;
      _log.i(s ? 'phone audio: silent' : 'phone audio: sound');
      notifyListeners();
    }
  }

  /// Capture ended outside the app (notification Stop, projection revoked): back to the mic,
  /// started once the app is in front again (a background mic needs its own service).
  void _onPhoneStopped() {
    _phoneSub = null;
    if (_disposed || source != AudioSourceKind.phone) return;
    _log.i('phone audio stopped');
    source = AudioSourceKind.mic;
    silenceHint = false;
    PlaybackCapture.overlayHide().catchError((_) {});
    notifyListeners();
    if (WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed) {
      _startMic().catchError((Object e) => _fail('Mic error: $e'));
    } else {
      _resumeOnce?.dispose();
      _resumeOnce = AppLifecycleListener(onResume: () {
        _resumeOnce?.dispose();
        _resumeOnce = null;
        if (source == AudioSourceKind.mic) {
          _startMic().catchError((Object e) => _fail('Mic error: $e'));
        }
      });
    }
  }

  /// 中文 / 原文 / 雙語 for the app and the overlay; saved with the overlay style.
  void setSubtitleMode(SubtitleMode m) => setOverlayStyle(overlayStyle.copyWith(mode: m));

  void setOverlayStyle(OverlayStyle s) {
    overlayStyle = s.clamp();
    _styleStore?.save(overlayStyle);
    PlaybackCapture.overlayStyle(overlayStyle).catchError((_) {});
    notifyListeners();
  }

  /// Every UI update also refreshes the floating overlay while phone audio is the source.
  @override
  void notifyListeners() {
    super.notifyListeners();
    if (source != AudioSourceKind.phone || _disposed) return;
    final lines = displayLines([for (final s in subtitles) (s.ru, s.zh)], overlayStyle.mode);
    final key = '${silenceHint ? 1 : 0}|${lines.join('\n')}';
    if (key == _overlaySent) return;
    _overlaySent = key;
    PlaybackCapture.overlayLines(lines, silenceHint ? silenceHintText : null).catchError((_) {});
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
    final jobs = _queue.toList();
    final plan = ContextBudget.trimPlan(
      [for (final j in jobs) j.ru],
      [for (final j in jobs) j.lang],
      maxQueue,
    );
    if (jobs.length > maxQueue && plan.texts.length > maxQueue) {
      _log.i('queue>$maxQueue, no same-language pair fits ${ContextBudget.maxMergeChars} chars');
    }
    for (final i in plan.steps) {
      final a = jobs[i];
      final b = jobs[i + 1];
      b.ru = ContextBudget.join(a.ru, b.ru);
      b.sub.ru = b.ru;
      subtitles.remove(a.sub);
      final merged = _Job(b.ru, b.sub, a.speechEndMs, b.asrFinalMs, b.lang);
      jobs
        ..[i] = merged
        ..removeAt(i + 1);
      dropped++;
      _log.i('queue>$maxQueue, merged jobs $i+${i + 1} -> ${AppLog.text(merged.ru)}');
    }
    if (plan.steps.isNotEmpty) {
      _queue
        ..clear()
        ..addAll(jobs);
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
          final r = await translateLine(_tr!, job.ru, job.lang, tgt: target);
          final now = _clock.elapsedMilliseconds;
          lastLatencyMs = now - job.speechEndMs;
          lastAfterFinalMs = now - job.asrFinalMs;
          lastTps = r.tokensPerSecond;
          lastTokens = r.tokens;
          lastFirstTokenMs = r.firstTokenMs;
          job.sub.zh = r.zh;
          _trimSubtitles();
          job.sub.traditionalOk = target != 'zh' || r.check.isTraditional;
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
    _resumeOnce?.dispose();
    if (_phoneSub != null || source == AudioSourceKind.phone) {
      _phoneSub?.cancel();
      PlaybackCapture.stop().catchError((_) {});
    }
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
