import 'dart:async';

/// Holds the one loaded read-aloud voice (#1280) and which language it is for.
///
/// Loading a voice takes a while (a worker isolate + a 110-140 MB model), and the user can change
/// 對方語言 or leave 對話翻譯 meanwhile. So every load carries a generation: a load that finishes
/// after the slot moved on is disposed instead of becoming the voice, and at most one voice is
/// alive. Pure (the voice type and its loader are parameters), see the tests.
class VoiceSlot<T> {
  VoiceSlot({required this.dispose});

  /// Frees a voice (stops its worker).
  final void Function(T voice) dispose;

  T? _voice;
  String? _lang;
  int _gen = 0;
  Future<void>? _loading;

  /// Language the slot is set to (loaded, loading, or tried without success); null = empty.
  String? get lang => _lang;
  bool get loading => _loading != null;

  /// The loaded voice when it is the one for [lang], else null.
  T? voiceFor(String lang) => _lang == lang ? _voice : null;

  /// Makes [lang] the wanted voice. Loads it with [load] unless it is loaded or loading already;
  /// any other voice is disposed first. [load] returns null when the voice is not installed, and
  /// may throw: both leave the slot without a voice, and the next call tries again.
  Future<void> ensure(String lang, Future<T?> Function() load) {
    if (_lang == lang) {
      if (_loading != null) return _loading!;
      if (_voice != null) return Future.value();
    }
    clear();
    _lang = lang;
    final gen = _gen;
    return _loading = _run(gen, load);
  }

  Future<void> _run(int gen, Future<T?> Function() load) async {
    try {
      await null; // [_loading] is set before even a synchronous failure reaches `finally`
      final v = await load();
      if (gen != _gen) {
        if (v != null) dispose(v); // the slot moved on while this one loaded
        return;
      }
      _voice = v;
    } finally {
      if (gen == _gen) _loading = null;
    }
  }

  /// Disposes the voice and forgets any load in flight (its result is disposed when it arrives).
  void clear() {
    _gen++;
    final v = _voice;
    _voice = null;
    _lang = null;
    _loading = null;
    if (v != null) dispose(v);
  }
}
