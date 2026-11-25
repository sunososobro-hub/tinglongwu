import 'dart:async';
import 'dart:collection';
import 'dart:typed_data';

/// Conversation mode read-aloud (#558, part 2): pure logic for the voice pack layout, the echo
/// guard and the queue of lines to speak. Testable without Flutter or a voice model.

/// Offline voice for one language, one of two sherpa-onnx engines:
///  - `vits` (Piper-style VITS, e.g. the English vits-ljs): model.onnx + tokens.txt + lexicon.txt.
///    sherpa-onnx 2.x dropped espeak-ng, so the pack must ship `lexicon.txt` (word -> phonemes).
///  - `supertonic` (Supertonic 3, 31 languages incl. Russian, works on plain text, no lexicon):
///    four int8 onnx parts + tts.json + unicode_indexer.bin + voice.bin.
///
/// Layout, relative to the models dir (or a Play pack folder, like the other models): every file
/// of the voice in `tts-LANG/`. Which voice ships, its licence, size and SHA-256:
/// `assets/voice_packs.json` (imported like the models, see ModelManifest.packs); until it is
/// installed the app shows the translation only.
class TtsVoiceLayout {
  static const vits = 'vits';
  static const supertonic = 'supertonic';

  /// Engines in the order a folder is probed.
  static const engines = [vits, supertonic];

  static const _files = {
    vits: ['model.onnx', 'tokens.txt', 'lexicon.txt'],
    supertonic: [
      'duration_predictor.int8.onnx',
      'text_encoder.int8.onnx',
      'vector_estimator.int8.onnx',
      'vocoder.int8.onnx',
      'tts.json',
      'unicode_indexer.bin',
      'voice.bin',
    ],
  };

  /// File names of [engine]'s voice (the VITS ones by default).
  static List<String> files([String engine = vits]) => _files[engine] ?? const [];

  static String dir(String lang) => 'tts-$lang';

  /// Manifest-style relative paths of the [engine] voice for [lang].
  static List<String> rel(String lang, {String engine = vits}) =>
      [for (final f in files(engine)) '${dir(lang)}/$f'];

  /// Supertonic voice style (0-9, one of the ten in voice.bin) and denoising steps per line
  /// (sherpa-onnx default 5: faster than real time on a desktop CPU, a phone is slower).
  static const supertonicSpeaker = 6;
  static const supertonicSteps = 5;
}

/// Absolute paths of an installed voice.
class TtsVoicePaths {
  const TtsVoicePaths({required this.lang, required this.engine, required this.files});
  final String lang;
  final String engine;

  /// File name -> absolute path (see [TtsVoiceLayout.files]).
  final Map<String, String> files;

  String get model => files['model.onnx'] ?? '';
  String get tokens => files['tokens.txt'] ?? '';
  String get lexicon => files['lexicon.txt'] ?? '';

  /// [resolve] maps a relative path to the file to read; [exists] says whether it is there.
  /// Null when no engine's files are all there (no partial voice).
  static TtsVoicePaths? find(
    String lang, {
    required String Function(String rel) resolve,
    required bool Function(String path) exists,
  }) {
    for (final engine in TtsVoiceLayout.engines) {
      final names = TtsVoiceLayout.files(engine);
      final p = [for (final r in TtsVoiceLayout.rel(lang, engine: engine)) resolve(r)];
      if (p.every(exists)) {
        return TtsVoicePaths(
            lang: lang, engine: engine, files: {for (var i = 0; i < names.length; i++) names[i]: p[i]});
      }
    }
    return null;
  }
}

/// Keeps the phone from translating its own voice: while a line is read aloud (plus a short
/// tail for the speaker's output latency and room echo) the mic audio is replaced by silence.
/// Silence instead of dropping, so the VAD still sees the speech end and closes the segment.
class EchoGuard {
  /// AudioTrack output latency is typically 50-200 ms on Android; 300 ms covers it with room
  /// echo. Not tuned on a device yet: the owner's phone test (#558) decides the final value.
  static const tailMs = 300;

  int _untilMs = -1;

  /// A line of [durationMs] started playing at [startMs].
  void playing({required int startMs, required int durationMs}) {
    final end = startMs + durationMs + tailMs;
    if (end > _untilMs) _untilMs = end;
  }

  /// Playback was stopped early at [nowMs]: only the tail is still guarded.
  void stopped(int nowMs) {
    final end = nowMs + tailMs;
    if (end < _untilMs) _untilMs = end;
  }

  bool muted(int nowMs) => nowMs < _untilMs;

  /// The mic chunk to feed the ASR: unchanged, or zeros of the same length while [muted].
  Uint8List filter(Uint8List pcm, int nowMs) => muted(nowMs) ? Uint8List(pcm.length) : pcm;
}

/// One translated line to read aloud.
class Utterance {
  const Utterance(this.text, {required this.lang, required this.speechEndMs, this.mtDoneMs = -1});
  final String text;

  /// Language of [text] (picks the voice).
  final String lang;

  /// Estimated time the speaker stopped talking (for the end-of-speech -> audio latency).
  final int speechEndMs;

  /// Time the translation was ready (-1: unknown), logged with VAD end and play start (#1953).
  final int mtDoneMs;
}

/// Text worth sending to the TTS: trimmed; '' when it has no letter or digit (just punctuation).
String speakableText(String s) {
  final t = s.trim();
  return RegExp(r'[\p{L}\p{N}]', unicode: true).hasMatch(t) ? t : '';
}

/// Lines waiting to be spoken, oldest first. When the conversation runs ahead of the voice the
/// oldest lines are dropped (still shown as text): hearing a stale line late is worse than not.
class SpeakQueue {
  static const maxPending = 2;

  final Queue<Utterance> _q = Queue();
  int dropped = 0;

  int get length => _q.length;
  bool get isEmpty => _q.isEmpty;

  /// Adds [u] unless it has nothing speakable. Returns whether it was queued.
  bool add(Utterance u) {
    final t = speakableText(u.text);
    if (t.isEmpty) return false;
    _q.add(Utterance(t, lang: u.lang, speechEndMs: u.speechEndMs, mtDoneMs: u.mtDoneMs));
    while (_q.length > maxPending) {
      _q.removeFirst();
      dropped++;
    }
    return true;
  }

  Utterance? next() => _q.isEmpty ? null : _q.removeFirst();

  void clear() => _q.clear();
}

/// Plays one line at a time (#1953): a line starts only after the previous one has finished,
/// so a long sentence is never cut by the next one and two lines never sound together.
/// [start] begins playback (true = started); [finished] completes when that line has ended.
class SerialSpeaker {
  SerialSpeaker({required this.start, required this.finished});
  final Future<bool> Function(Float32List samples, int sampleRate) start;
  final Future<void> Function(int durationMs) finished;

  Future<void> _tail = Future<void>.value();
  int _gen = 0;

  /// Completes when every line queued so far has ended.
  Future<void> get done => _tail;

  /// Queues a line; the future says whether it started (false: failed or [stop]ped first).
  Future<bool> play(Float32List samples, int sampleRate, int durationMs) {
    final gen = _gen;
    final prev = _tail;
    final started = Completer<bool>();
    final ended = Completer<void>();
    _tail = ended.future; // set before any await: concurrent calls chain, never overlap
    unawaited(() async {
      await prev;
      var ok = false;
      if (gen == _gen) {
        try {
          ok = await start(samples, sampleRate);
        } catch (_) {}
      }
      started.complete(ok);
      if (ok) {
        try {
          await finished(durationMs);
        } catch (_) {}
      }
      ended.complete();
    }());
    return started.future;
  }

  /// Drops the lines still waiting (the caller stops the one playing).
  void stop() => _gen++;
}

/// Earphone-only (#1953, docs/accessibility-listen-speak.md section 3): reading a translation
/// aloud next to the original is confusing, so by default it is only spoken into a headset.
/// Face-to-face conversation is the exception: the other person must hear the phone speaker.
bool earphoneOnlyFor({required bool conversation}) => !conversation;

/// Whether a line may be spoken. [headset] null = unknown (no native check): not held back.
bool speakAllowed({required bool? headset, required bool earphoneOnly}) =>
    !earphoneOnly || headset != false;

/// Pill suffix in 對話模式: whether the translation of my lines is read aloud.
/// [available] = a voice pack for the other person's language exists (false: nothing to install).
String voiceStatusLabel({required bool installed, required bool speaking, bool available = true}) {
  if (!installed) return available ? '未安裝語音包，只顯示文字' : '只顯示文字（這個語言還沒有語音）';
  return speaking ? '朗讀中' : '朗讀';
}
