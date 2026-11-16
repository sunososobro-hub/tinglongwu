import 'dart:async';
import 'dart:collection';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:record/record.dart';

import '../logic/context_budget.dart';
import '../logic/device_probe.dart';
import '../logic/draft_gate.dart';
import '../logic/feature_prefs.dart';
import '../logic/lang_pack.dart';
import '../logic/phone_audio.dart';
import '../logic/s2tw.dart';
import '../logic/segmenter.dart';
import '../logic/speakers.dart';
import '../logic/subtitle_trim.dart';
import 'app_log.dart';
import 'device_probe_service.dart';
import 'asr_worker.dart';
import 'speaker_worker.dart';
import 'model_importer.dart';
import 'model_paths.dart';
import 'play_packs.dart';
import 'playback_capture.dart';
import '../logic/conversation.dart';
import '../logic/speech_out.dart';
import '../logic/target_language.dart';
import 'translator.dart';
import 'tts_player.dart';
import 'tts_worker.dart';

enum Stage { starting, downloadingModels, loadingModels, listening, missingModels, error }

/// Where the audio comes from: the microphone, or other apps' playback (#998 Android 10+,
/// #1076 iOS 15+ via a broadcast extension).
enum AudioSourceKind { mic, phone }

/// Shown instead of an empty overlay when phone audio stays silent (app blocks capture).
const silenceHintText = '沒有聽到影片的聲音：這個 App 可能不允許翻譯（例如有版權保護的影片），或影片還沒開始播放';

class Subtitle {
  Subtitle(this.ru,
      {this.zh, this.traditionalOk = true, this.speaker = Speaker.them, this.seg = -1});
  String ru;
  String? zh;
  bool traditionalOk;

  /// Conversation mode (#558): my lines show on the right; listen-only lines are all [Speaker.them].
  final Speaker speaker;

  /// VAD segment this line came from, and the voice number found for it (#1155; 0 = none yet).
  final int seg;
  int voice = 0;
}

class _Job {
  _Job(this.ru, this.sub, this.speechEndMs, this.asrFinalMs, this.route);

  /// Direction fixed when the line was heard, so a side switch never re-routes queued lines.
  final ConversationRoute route;
  String get lang => route.src;
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

  /// First-run Play download (Stage.downloadingModels): bytes, total, PlayPacks.kt state name,
  /// and the error (network, storage, cancelled, Play code) once it stopped.
  int dlDone = 0, dlTotal = 0;
  String dlState = 'pending';
  String? dlError;
  ModelPaths? paths;

  /// Grey text under construction: the draft (#1064) or '…' while speech goes on.
  String draftRu = '';
  final DraftTracker _draft = DraftTracker();
  Timer? _draftExpiry;
  FeaturePrefs prefs = const FeaturePrefs();
  FeaturePrefsStore? _prefsStore;

  /// 中文草稿 (#1065): translation of the current draft (gray), '' = none.
  String draftTr = '';
  int? _draftTrSeg;
  final DraftTranslateGate _trGate = DraftTranslateGate();
  bool _draftTrStop = false;

  /// Device probe + benchmark (#1065). [probe].bench is null until the test has run.
  ProbeReport? probe;
  bool benchRunning = false;
  ProbeStore? _probeStore;
  Completer<AsrBench>? _benchWait;
  Timer? _benchTimer;
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

  /// Face-to-face conversation mode (#558); off = listen-only.
  Conversation conversation = const Conversation();
  ConversationStore? _conversationStore;

  /// Turns 對話模式 on or off (remembered).
  void setConversationEnabled(bool on) {
    _setConversation(conversation.copyWith(enabled: on));
    if (on) {
      if (_tr != null) _ensureTts();
    } else {
      _stopSpeaking();
    }
  }

  /// Two-way mode: my language / the partner's language (remembered).
  void setMineLang(String code) => _setConversation(conversation.copyWith(mineLang: code));
  void setPartnerLang(String code) {
    final before = conversation.foreign;
    _setConversation(conversation.copyWith(foreign: code));
    if (conversation.foreign == before) return;
    // The read-aloud voice follows the partner's language: drop the old one, load on demand.
    _tts?.dispose();
    _tts = null;
    _ttsLoading = null;
    ttsInstalled = false;
    if (conversation.enabled && _tr != null) _ensureTts();
  }

  /// Direction picked from the detected language (true) or the manual side button (false).
  void setAutoDirection(bool on) => _setConversation(conversation.copyWith(auto: on));

  /// One tap: the other side is talking now.
  void switchSpeaker() => _setConversation(conversation.switched());

  void _setConversation(Conversation c) {
    if (c == conversation) return;
    conversation = c;
    _clearDraftTr(); // the draft was translated for the other direction
    _syncAsrRoute();
    final store = _conversationStore; // null until start() (and AppLog) ran
    if (store == null) return notifyListeners();
    store.save(c);
    _log.i('conversation ${c.enabled ? "on" : "off"} side=${c.side.name} foreign=${c.foreign} mine=${c.mineLang} auto=${c.auto}');
    notifyListeners();
  }

  // ---- extra-language recognizer (#556 part 2: Russian)

  /// The other person's language needs a recognizer pack that is not installed (or failed to
  /// load): the pill says so, and their lines are dropped (SenseVoice would only make garbage).
  bool extraAsrMissing = false;

  /// Tells the ASR isolate to load (only while 對話翻譯 is on with such a language, it costs
  /// memory) or free the extra recognizer, and how to route the segments.
  void _syncAsrRoute() {
    final c = _activeConversation;
    final need = c.enabled && Conversation.needsExtraAsr.contains(c.foreign);
    final pack = need ? paths?.asrPack(c.foreign) : null;
    extraAsrMissing = need && pack == null && paths != null;
    _asr?.setRoute(pack == null
        ? null
        : AsrRoute(pack: pack, mine: c.mineLang, force: c.auto ? null : c.side == Speaker.them));
  }

  // ---- read-aloud (#558 part 2)

  /// The voice for the other person's language is installed (else my lines are text only).
  bool ttsInstalled = false;

  /// Languages that have a read-aloud voice pack (assets/voice_packs.json); null = not known.
  Set<String>? voiceLangs;

  /// A voice pack exists for the other person's language (false: nothing to install).
  bool get voiceAvailable => voiceLangs?.contains(conversation.foreign) ?? true;

  /// 對話翻譯 lacks a pack the user can install for the other person's language (#1262).
  bool get packMissing => extraAsrMissing || (!ttsInstalled && voiceAvailable);

  /// A line is being read aloud now.
  bool speaking = false;

  /// End of my speech -> start of the spoken translation, last line (ms).
  int? lastSpeakLatencyMs;
  TtsWorker? _tts;
  Future<void>? _ttsLoading;
  final SpeakQueue _speakQ = SpeakQueue();
  final EchoGuard _echo = EchoGuard();
  bool _speakBusy = false;

  /// Loads the voice once, the first time conversation mode needs it (a voice costs memory).
  Future<void> _ensureTts() => _ttsLoading ??= _loadTts();

  Future<void> _loadTts() async {
    final lang = conversation.foreign;
    final voice = paths?.ttsVoice(lang);
    ttsInstalled = voice != null;
    if (voice == null) {
      _log.i('tts: no $lang voice (tts-$lang/), text only');
      _ttsLoading = null; // look again next time (the pack may be imported meanwhile)
      if (!_disposed) notifyListeners();
      return;
    }
    try {
      final t0 = _clock.elapsedMilliseconds;
      final w = await TtsWorker.spawn(voice);
      if (_disposed) return w.dispose();
      _tts = w;
      _log.i('tts: $lang voice (${voice.engine}) ready in ${_clock.elapsedMilliseconds - t0} ms');
      _speakNext();
    } catch (e) {
      ttsInstalled = false;
      _ttsLoading = null;
      _log.e('tts: load failed: $e');
    }
    if (!_disposed) notifyListeners();
  }

  /// Queues a translated line of mine to be read aloud.
  void _say(Utterance u) {
    if (!_speakQ.add(u)) return;
    _ensureTts();
    _speakNext();
  }

  Future<void> _speakNext() async {
    final tts = _tts;
    if (_speakBusy || _disposed || tts == null) return;
    _speakBusy = true;
    try {
      while (!_disposed && !_speakQ.isEmpty) {
        final u = _speakQ.next()!;
        final a = await tts.synth(u.text);
        if (_disposed || !conversation.enabled) break;
        if (a.samples.isEmpty) continue;
        if (!await TtsPlayer.play(a.samples, a.sampleRate)) {
          _log.e('tts: playback failed');
          continue;
        }
        final start = _clock.elapsedMilliseconds;
        _echo.playing(startMs: start, durationMs: a.durationMs);
        lastSpeakLatencyMs = start - u.speechEndMs;
        speaking = true;
        notifyListeners();
        _log.i('TTS [${u.lang}] speakLat=${lastSpeakLatencyMs}ms gen=${a.genMs}ms '
            'audio=${a.durationMs}ms dropped=${_speakQ.dropped}: ${AppLog.text(u.text)}');
        await Future<void>.delayed(Duration(milliseconds: a.durationMs));
        speaking = false;
        if (!_disposed) notifyListeners();
      }
    } catch (e) {
      _log.e('tts: $e');
    } finally {
      _speakBusy = false;
      speaking = false;
    }
  }

  void _stopSpeaking() {
    _speakQ.clear();
    if (!speaking) return;
    speaking = false;
    _echo.stopped(_clock.elapsedMilliseconds);
    TtsPlayer.stop();
  }

  /// Mic audio for the ASR: silence while the phone reads a line aloud (no self-translation).
  void _onMicPcm(Uint8List b) => _asr?.feedPcm16(_echo.filter(b, _clock.elapsedMilliseconds));

  /// Conversation mode only applies to the microphone (face to face); 看影片翻譯 stays listen-only.
  Conversation get _activeConversation =>
      source == AudioSourceKind.mic ? conversation : conversation.copyWith(enabled: false);

  /// Translation target for the line being spoken now.
  String get activeTarget => _activeConversation.targetFor(target);

  Future<bool> _downloadPacks() async {
    stage = Stage.downloadingModels;
    dlError = null;
    dlState = 'pending';
    notifyListeners();
    final r = await PlayPacks.fetch(onState: (d, t, st) {
      dlDone = d;
      dlTotal = t;
      dlState = st;
      notifyListeners();
    });
    if (r.error == null) return true;
    _log.e('play packs: ${r.error} ${r.message ?? ''}');
    dlError = r.error;
    notifyListeners();
    return false;
  }

  Future<void> start() async {
    try {
      paths = await ModelPaths.resolve();
      await AppLog.init(paths!.filesDir);
      logPath = _log.path;
      _log.i('files dir ${paths!.filesDir}');
      _targetStore = TargetLanguageStore('${paths!.filesDir}/target_language.txt');
      target = _targetStore!.load();
      _prefsStore = FeaturePrefsStore('${paths!.filesDir}/features.txt');
      prefs = _prefsStore!.load();
      _conversationStore = ConversationStore('${paths!.filesDir}/conversation.txt');
      conversation = _conversationStore!.load();
      _styleStore = OverlayStyleStore('${paths!.filesDir}/overlay_style.txt');
      overlayStyle = _styleStore!.load();
      phoneSupported = await PlaybackCapture.supported();
      try {
        final m = await ModelImporter.loadManifest();
        voiceLangs = {for (final v in m.packs) if (!v.isAsr) v.lang};
      } catch (_) {} // unknown: the pill keeps saying the voice is not installed
      var missing = paths!.missing();
      if (missing.isNotEmpty && await PlayPacks.available()) {
        // Play install: the fast-follow packs may still be downloading. Wait here with a progress
        // screen; on failure the screen offers 「重試」 (= start() again).
        if (!await _downloadPacks()) return;
        paths = await ModelPaths.resolve();
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
          case AsrSpeech(:final speaking):
            if (_draft.onSpeech(speaking, _clock.elapsedMilliseconds)) _refreshDraft();
          case AsrDraft(:final seg, :final text):
            // Russian speech heard by SenseVoice is garbage: no draft for the other person then.
            if (prefs.sourceDraft &&
                !_activeConversation.foreignNeedsExtraAsr(e.lang) &&
                _draft.onDraft(seg, text)) {
              _refreshDraft();
              _maybeTranslateDraft(seg, text, e.lang);
            }
          case AsrBench():
            if (!(_benchWait?.isCompleted ?? true)) _benchWait!.complete(e);
          case AsrSegmentEnd(:final seg):
            if (_draft.onFinal(seg)) _refreshDraft();
          case AsrExtraState(:final lang, :final error):
            if (error == null) {
              _log.i('asr: $lang recognizer ready');
            } else {
              _log.e('asr: $lang recognizer failed: $error');
              extraAsrMissing = true;
              if (!_disposed) notifyListeners();
            }
          case AsrFinal():
            _draft.onFinal(e.seg);
            _refreshDraft(notify: false);
            if (extraAsrMissing && _activeConversation.foreignNeedsExtraAsr(e.lang)) {
              _log.i('ASR final dropped (no ${_activeConversation.foreign} recognizer): lang=${e.lang}');
              notifyListeners();
            } else {
              _onFinal(e);
            }
        }
      });
      await ready.future;
      _asr!.setDraft(prefs.sourceDraft);
      _syncAsrRoute();
      if (prefs.speakerLabels) _startSpeakers();
      _probeStore = ProbeStore('${p.filesDir}/device_probe.json');
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

      if (conversation.enabled) _ensureTts();
      _drain(); // anything queued while the LLM was loading (debug wavs)
      if (_disposed) return;
      await _startMic();
      stage = Stage.listening;
      _log.i('listening');
      notifyListeners();
      _startProbe();
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
    _micSub = stream.listen(_onMicPcm, onError: (Object e) => _fail('Mic error: $e'));
  }

  /// Footer mic button (#1121): true = listening is stopped (mic closed / phone audio dropped).
  bool paused = false;
  bool _pausing = false;

  /// Stops or resumes listening without leaving the screen. Mic: closes/reopens the recorder.
  /// Phone audio: the capture stays up, its audio is just dropped.
  Future<void> setPaused(bool v) async {
    if (v == paused || _pausing || _disposed || _asr == null || switchingSource) return;
    _pausing = true;
    paused = v;
    if (v) {
      _draft.reset();
      draftRu = '';
      silenceHint = false;
    }
    notifyListeners();
    try {
      if (source == AudioSourceKind.mic) {
        if (v) {
          await _stopMic();
        } else {
          await _startMic();
        }
      }
    } catch (e) {
      _log.e('setPaused($v) failed: $e');
      if (!v) _fail('Mic error: $e');
    } finally {
      _pausing = false;
      if (!_disposed) notifyListeners();
    }
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
    paused = false;
    switchingSource = true;
    notifyListeners();
    try {
      if (k == AudioSourceKind.phone) {
        if (!phoneSupported) return CapturePlatform.current().unsupportedText;
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
        _syncAsrRoute();
        silenceHint = false;
        _overlaySent = null;
        await PlaybackCapture.overlayStyle(overlayStyle);
        await PlaybackCapture.overlayShow();
        _log.i('source: phone audio');
      } else {
        source = AudioSourceKind.mic;
        _syncAsrRoute();
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
    if (paused) return;
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
    _syncAsrRoute();
    silenceHint = false;
    paused = false;
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

  /// 傾斜翻面 (#1146): auto-flip lock, remembered.
  void setTiltFlip(bool on) {
    if (on == prefs.tiltFlip) return;
    prefs = prefs.copyWith(tiltFlip: on);
    _prefsStore?.save(prefs);
    notifyListeners();
  }

  /// 說話者標籤 (#1155): opt-in, needs the optional speaker model. False = not turned on (model
  /// missing); the caller tells the user to import it.
  bool setSpeakerLabels(bool on) {
    if (on == prefs.speakerLabels) return true;
    if (on && !(paths?.hasSpeakerModel ?? false)) return false;
    prefs = prefs.copyWith(speakerLabels: on);
    _prefsStore?.save(prefs);
    if (on) {
      _startSpeakers();
    } else {
      _stopSpeakers();
    }
    notifyListeners();
    return true;
  }

  SpeakerWorker? _spk;
  StreamSubscription<SpeakerResult>? _spkSub;
  bool _spkStarting = false;
  final Map<int, int> _voiceBySeg = {};

  /// True while labels are on and the worker runs.
  bool get speakerLabelsActive => _spk != null;

  Future<void> _startSpeakers() async {
    final p = paths;
    if (_spk != null || _spkStarting || _asr == null || p == null || !p.hasSpeakerModel) return;
    _spkStarting = true;
    try {
      final w = await SpeakerWorker.spawn(p.speakerPath);
      if (w == null || _disposed || !prefs.speakerLabels || _asr == null) {
        w?.dispose();
        if (w == null) _log.e('speaker model failed to load; labels stay off');
        return;
      }
      _spk = w;
      _spkSub = w.results.listen(_onVoice);
      _asr!.setSpeakerPort(w.port);
      _log.i('speaker labels on');
    } catch (e) {
      _log.e('speaker labels failed: $e');
    } finally {
      _spkStarting = false;
    }
  }

  void _stopSpeakers() {
    _asr?.setSpeakerPort(null);
    _spkSub?.cancel();
    _spkSub = null;
    _spk?.dispose();
    _spk = null;
    _voiceBySeg.clear();
    for (final s in subtitles) {
      s.voice = 0;
    }
  }

  void _onVoice(SpeakerResult r) {
    if (r.speaker == unknownSpeaker || !prefs.speakerLabels) return;
    _voiceBySeg[r.seg] = r.speaker;
    if (_voiceBySeg.length > 32) _voiceBySeg.remove(_voiceBySeg.keys.first);
    var hit = false;
    for (final s in subtitles) {
      if (s.seg == r.seg) {
        s.voice = r.speaker;
        hit = true;
      }
    }
    if (hit) notifyListeners();
  }

  /// 顯示原文草稿 (#1064): turns the grey interim source text on or off, remembered.
  void setSourceDraft(bool on) {
    if (on == prefs.sourceDraft) return;
    prefs = prefs.copyWith(sourceDraft: on);
    _prefsStore?.save(prefs);
    _asr?.setDraft(on);
    if (!on) _draft.reset();
    _refreshDraft();
  }

  /// 中文草稿 (#1065): translate the draft at most every 2 s (default off, the user decides).
  void setZhDraft(bool on) {
    if (on == prefs.zhDraft) return;
    prefs = prefs.copyWith(zhDraft: on);
    _prefsStore?.save(prefs);
    _trGate.reset();
    if (!on) _clearDraftTr();
    notifyListeners();
  }

  void _clearDraftTr() {
    draftTr = '';
    _draftTrSeg = null;
  }

  /// Translates [text] (a draft) when the translator is idle; a final line arriving meanwhile
  /// stops it at the next token, so it never delays the final path.
  Future<void> _maybeTranslateDraft(int seg, String text, String lang) async {
    final tr = _tr;
    if (!prefs.zhDraft || !prefs.sourceDraft || tr == null || _disposed) return;
    final now = _clock.elapsedMilliseconds;
    if (!_trGate.shouldRun(
        nowMs: now, text: text, translatorBusy: _busy || _queue.isNotEmpty || benchRunning)) {
      return;
    }
    _trGate.started(nowMs: now, text: text);
    _draftTrStop = false;
    try {
      final r = await tr.translate(text, src: lang, tgt: _activeConversation.route(lang, listenTarget: target).tgt, shouldStop: () => _draftTrStop);
      if (!_draftTrStop && !_disposed && !_draft.isStale(seg) && r.zh.trim().isNotEmpty) {
        draftTr = r.zh.trim();
        _draftTrSeg = seg;
        notifyListeners();
      }
    } catch (e) {
      _log.e('draft translate failed: $e'); // a failed draft is just skipped
    } finally {
      _trGate.finished();
    }
  }

  // ---- device probe (#1065)

  /// Loads the cached probe for this app version, or schedules the benchmark in the background.
  Future<void> _startProbe() async {
    final info = await DeviceProbeService.collect();
    final cached = _probeStore?.load(info.appVersion);
    probe = cached ?? ProbeReport(info, null);
    if (!_disposed) notifyListeners();
    if (cached == null) _scheduleBench(const Duration(seconds: 8));
  }

  void _scheduleBench(Duration after, {int tries = 0}) {
    _benchTimer?.cancel();
    _benchTimer = Timer(after, () async {
      if (_disposed || benchRunning) return;
      // Wait for a quiet moment: no speech, nothing translating.
      if (_busy || _queue.isNotEmpty || _draft.speaking || _trGate.busy) {
        if (tries < 8) _scheduleBench(const Duration(seconds: 10), tries: tries + 1);
        return;
      }
      final ok = await runBenchmark();
      if (!ok && !_disposed && tries < 8) {
        _scheduleBench(const Duration(seconds: 10), tries: tries + 1);
      }
    });
  }


  /// Runs the speed test (also from the settings page). ASR on 4 s of synthetic audio, then one
  /// fixed sentence through the translator; about 5 s. Returns false when skipped or failed.
  Future<bool> runBenchmark() async {
    final asr = _asr;
    final tr = _tr;
    if (asr == null || tr == null || benchRunning || _busy || _disposed) return false;
    benchRunning = true;
    _busy = true; // the translator is ours until we are done
    _draftTrStop = true;
    notifyListeners();
    while (_trGate.busy && !_disposed) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    final sw = Stopwatch()..start();
    var ok = false;
    try {
      _benchWait = Completer<AsrBench>();
      asr.bench();
      final a = await _benchWait!.future.timeout(const Duration(seconds: 20));
      if (a.busy) return false;
      const sentence = 'The weather is nice today, so let us take a walk by the river.';
      final r = await tr.translate(sentence, src: 'en', tgt: 'zh');
      final chars = r.zh.runes.length;
      if (r.totalMs <= 0 || chars == 0 || a.decodeMs <= 0) return false;
      final bench = BenchResult(
        asrRtf: a.rtf,
        llmCharsPerSec: chars * 1000 / r.totalMs,
        llmTokensPerSec: r.tokensPerSecond,
        totalMs: sw.elapsedMilliseconds,
      );
      final base = (await DeviceProbeService.collect());
      probe = ProbeReport(base, bench);
      _probeStore?.save(base, bench);
      _log.i('bench: asr rtf=${bench.asrRtf.toStringAsFixed(3)} '
          'llm ${bench.llmCharsPerSec.toStringAsFixed(1)} chars/s '
          '${bench.llmTokensPerSec.toStringAsFixed(1)} tok/s total=${bench.totalMs}ms '
          '${base.summary()}');
      ok = true;
    } catch (e) {
      _log.e('bench failed: $e');
    } finally {
      _benchWait = null;
      benchRunning = false;
      _busy = false;
      if (!_disposed) {
        notifyListeners();
        _drain(); // lines that arrived during the test
      }
    }
    return ok;
  }

  /// Recomputes [draftRu] from the tracker; the held draft expires by a timer.
  void _refreshDraft({bool notify = true}) {
    final now = _clock.elapsedMilliseconds;
    final d = prefs.sourceDraft ? _draft.shown(now) : '';
    draftRu = d.isNotEmpty ? d : (_draft.speaking ? '…' : '');
    if (d.isEmpty || (_draftTrSeg != null && _draft.isStale(_draftTrSeg!))) _clearDraftTr();
    _draftExpiry?.cancel();
    final at = _draft.expiresAtMs();
    if (at != null && prefs.sourceDraft) {
      _draftExpiry = Timer(Duration(milliseconds: at - now), () {
        if (!_disposed) _refreshDraft();
      });
    }
    if (notify && !_disposed) notifyListeners();
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
    // Grey draft on the source line; hidden in 中文-only mode (no source text is shown there).
    final draft = (prefs.sourceDraft && overlayStyle.mode != SubtitleMode.zh && draftRu != '…')
        ? draftRu
        : '';
    final dTr = draft.isNotEmpty && prefs.zhDraft ? draftTr : '';
    final key = '${silenceHint ? 1 : 0}|${lines.join('\n')}|$draft|$dTr';
    if (key == _overlaySent) return;
    _overlaySent = key;
    PlaybackCapture.overlayLines(lines, silenceHint ? silenceHintText : null, draft: draft, draftTr: dTr)
        .catchError((_) {});
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
    // One subtitle (and one LLM call) per sentence: short lines update fast and the model never
    // sees several sentences to smooth together.
    final sentences = SentenceSegmenter.split(f.text);
    final route = _activeConversation.route(f.lang, listenTarget: target);
    for (final text in sentences.isEmpty ? [f.text] : sentences) {
      final sub = Subtitle(displayOriginal(text, f.lang), speaker: route.speaker, seg: f.seg);
      sub.voice = _voiceBySeg[f.seg] ?? 0;
      subtitles.add(sub);
      _queue.add(_Job(text, sub, speechEnd, now, route));
    }
    _trimSubtitles();
    // Bound the backlog: merge the oldest two waiting jobs (keeps all text,
    // costs one fewer LLM call) instead of dropping. A merge that would be
    // longer than ContextBudget.maxMergeChars is skipped (#533): it could
    // overflow the LLM context and stop the app.
    final jobs = _queue.toList();
    final plan = ContextBudget.trimPlan(
      [for (final j in jobs) j.ru],
      [for (final j in jobs) j.route.mergeKey],
      maxQueue,
    );
    if (jobs.length > maxQueue && plan.texts.length > maxQueue) {
      _log.i('queue>$maxQueue, no same-language pair fits ${ContextBudget.maxMergeChars} chars');
    }
    for (final i in plan.steps) {
      final a = jobs[i];
      final b = jobs[i + 1];
      b.ru = ContextBudget.join(a.ru, b.ru);
      b.sub.ru = displayOriginal(b.ru, b.lang);
      subtitles.remove(a.sub);
      final merged = _Job(b.ru, b.sub, a.speechEndMs, b.asrFinalMs, b.route);
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
      _draftTrStop = true; // a draft translation yields to the final line
      while (_trGate.busy && !_disposed) {
        await Future<void>.delayed(const Duration(milliseconds: 10));
      }
      while (_queue.isNotEmpty && !_disposed) {
        final job = _queue.removeFirst();
        queued = _queue.length + 1;
        notifyListeners();
        try {
          final tgt = job.route.tgt;
          final r = await translateLine(_tr!, job.ru, job.lang, tgt: tgt);
          final now = _clock.elapsedMilliseconds;
          lastLatencyMs = now - job.speechEndMs;
          lastAfterFinalMs = now - job.asrFinalMs;
          lastTps = r.tokensPerSecond;
          lastTokens = r.tokens;
          lastFirstTokenMs = r.firstTokenMs;
          job.sub.zh = r.zh;
          _trimSubtitles();
          job.sub.traditionalOk = tgt != 'zh' || r.check.isTraditional;
          if (job.route.speak && conversation.enabled) {
            _say(Utterance(r.zh, lang: tgt, speechEndMs: job.speechEndMs));
          }
          final tag = tgt != 'zh'
              ? '${job.lang}->$tgt${job.route.speak ? " speak" : ""}'
              : (r.check.isTraditional ? 'zh-Hant' : 'NOT-TRAD ${r.check}');
          _log.i('${tgt.toUpperCase()} [$tag'
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
    _draftExpiry?.cancel();
    _benchTimer?.cancel();
    _resumeOnce?.dispose();
    if (_phoneSub != null || source == AudioSourceKind.phone) {
      _phoneSub?.cancel();
      PlaybackCapture.stop().catchError((_) {});
    }
    _rec.dispose();
    _spkSub?.cancel();
    _spk?.dispose();
    _asr?.dispose();
    _tr?.dispose();
    _tts?.dispose();
    _speakQ.clear();
    TtsPlayer.stop();
    try {
      _log.i('=== session end ===');
      _log.flush();
    } catch (_) {}
    super.dispose();
  }
}
