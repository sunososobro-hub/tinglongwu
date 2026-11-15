import 'dart:typed_data';

/// Pure logic for the grey source-text draft (#1064): which audio to re-recognize, when to run,
/// and how a late draft is replaced by the final text. No Flutter / sherpa imports.

/// Throttle for draft decodes. At most one decode runs at a time (the next one is skipped, never
/// queued), runs are at least [intervalMs] apart, and a run needs [minAudioSamples] of audio and
/// [minGrowSamples] more audio than the previous run of the same segment.
class DraftGate {
  DraftGate({
    this.intervalMs = 800,
    this.minAudioSamples = 9600, // 0.6 s at 16 kHz
    this.minGrowSamples = 4800, // 0.3 s
  });

  final int intervalMs;
  final int minAudioSamples;
  final int minGrowSamples;

  bool _busy = false;
  int _lastStartMs = -(1 << 40);
  int _lastSamples = 0;

  bool get busy => _busy;

  bool shouldRun({required int nowMs, required int samples}) {
    if (_busy) return false;
    if (samples < minAudioSamples) return false;
    if (samples - _lastSamples < minGrowSamples) return false;
    return nowMs - _lastStartMs >= intervalMs;
  }

  void started({required int nowMs, required int samples}) {
    _busy = true;
    _lastStartMs = nowMs;
    _lastSamples = samples;
  }

  /// The decode finished (or failed): the next run is allowed after the interval.
  void finished() => _busy = false;

  /// A new segment begins (the previous one was finalized): audio counts from zero again.
  void segmentReset() => _lastSamples = 0;
}

/// Accumulates the audio of the speech segment in progress, with a short pre-roll (VAD reports
/// speech a little after it starts) and a cap so a stuck VAD cannot grow it without bound.
class DraftAudio {
  DraftAudio({this.prerollSamples = 4000, this.maxSamples = 16000 * 7});

  final int prerollSamples;
  final int maxSamples;

  final List<Float32List> _pre = [];
  int _preLen = 0;
  final List<Float32List> _chunks = [];
  int _len = 0;
  bool _active = false;

  int get length => _len;
  bool get active => _active;

  void feed(Float32List s, {required bool detected}) {
    if (detected) {
      if (!_active) {
        _active = true;
        for (final c in _pre) {
          _chunks.add(c);
          _len += c.length;
        }
      }
      _chunks.add(s);
      _len += s.length;
      while (_len > maxSamples && _chunks.length > 1) {
        _len -= _chunks.removeAt(0).length;
      }
      _pre.clear();
      _preLen = 0;
    } else {
      _active = false;
      _chunks.clear();
      _len = 0;
      _pre.add(s);
      _preLen += s.length;
      while (_preLen > prerollSamples && _pre.length > 1) {
        _preLen -= _pre.removeAt(0).length;
      }
    }
  }

  /// The VAD popped a segment: start counting from empty (speech may still go on).
  void segmentDone() {
    _chunks.clear();
    _len = 0;
  }

  Float32List snapshot() {
    final out = Float32List(_len);
    var o = 0;
    for (final c in _chunks) {
      out.setRange(o, o + c.length, c);
      o += c.length;
    }
    return out;
  }
}

/// What the UI shows as the draft. Segment ids count VAD segments in order; a draft decoded for a
/// segment that was already finalized (it arrives from another isolate, so it can be late) is
/// dropped, and the final text always replaces the draft.
class DraftTracker {
  /// Keep a draft this long after speech ended, waiting for its final; then give up (VAD may
  /// discard a too-short segment and never send a final).
  static const holdAfterSpeechEndMs = 2500;

  int _finalSeg = -1;
  String _text = '';
  bool _speaking = false;
  int? _endedAtMs;

  /// Latest draft text (may be stale, see [shown]).
  String get text => _text;
  bool get speaking => _speaking;

  /// Returns true when what [shown] returns may have changed.
  bool onSpeech(bool speaking, int nowMs) {
    if (speaking == _speaking) return false;
    _speaking = speaking;
    _endedAtMs = speaking ? null : nowMs;
    return true;
  }

  /// True when segment [seg] was already finalized (a result computed for it is stale).
  bool isStale(int seg) => seg <= _finalSeg;

  bool onDraft(int seg, String text) {
    if (seg <= _finalSeg) return false;
    if (text == _text) return false;
    _text = text;
    return true;
  }

  /// The segment [seg] was finalized (with or without text).
  bool onFinal(int seg) {
    if (seg > _finalSeg) _finalSeg = seg;
    if (_text.isEmpty) return false;
    _text = '';
    return true;
  }

  /// The draft to display at [nowMs] ('' = none).
  String shown(int nowMs) {
    final e = _endedAtMs;
    if (e != null && nowMs - e > holdAfterSpeechEndMs) return '';
    return _text;
  }

  /// When the held draft stops being shown (ms), or null if it is not waiting to expire.
  int? expiresAtMs() => (_endedAtMs != null && _text.isNotEmpty) ? _endedAtMs! + holdAfterSpeechEndMs + 1 : null;

  void reset() {
    _finalSeg = -1;
    _text = '';
    _speaking = false;
    _endedAtMs = null;
  }
}

/// Throttle for 中文草稿 (#1065): translate the draft at most every [intervalMs], only when the
/// translator is idle (no final line queued or running, no draft translation running), only for
/// text that changed and is long enough to mean something.
class DraftTranslateGate {
  DraftTranslateGate({this.intervalMs = 2000, this.minChars = 6});

  final int intervalMs;
  final int minChars;

  bool _busy = false;
  int _lastStartMs = -(1 << 40);
  String _lastText = '';

  bool get busy => _busy;

  bool shouldRun({required int nowMs, required String text, required bool translatorBusy}) {
    if (_busy || translatorBusy) return false;
    final t = text.trim();
    if (t.runes.length < minChars || t == _lastText) return false;
    return nowMs - _lastStartMs >= intervalMs;
  }

  void started({required int nowMs, required String text}) {
    _busy = true;
    _lastStartMs = nowMs;
    _lastText = text.trim();
  }

  void finished() => _busy = false;

  /// Forget the last text (new sentence or the feature was switched).
  void reset() => _lastText = '';
}
