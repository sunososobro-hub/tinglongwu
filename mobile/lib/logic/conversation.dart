import 'dart:io';

/// Face-to-face conversation mode (#558, part 1): the owner speaks Chinese and the phone shows
/// (later: speaks) the translation in the other person's language; the other person speaks their
/// language and the owner reads zh-Hant. Pure logic + a small text file, testable without Flutter.
///
/// Off by default: the app stays listen-only (every line -> the picked subtitle language).
enum Speaker { me, them }

/// Where one finished ASR line goes.
class ConversationRoute {
  const ConversationRoute({
    required this.src,
    required this.tgt,
    required this.speaker,
    required this.speak,
  });

  /// ASR-detected source language (the text is in this language whatever the side says).
  final String src;

  /// Translation target.
  final String tgt;
  final Speaker speaker;

  /// The translation should be read aloud on the phone speaker (when the voice pack is installed).
  final bool speak;

  /// Key for job merging: only lines with the same source and target may be merged.
  String get mergeKey => '$src>$tgt';

  @override
  bool operator ==(Object other) =>
      other is ConversationRoute &&
      other.src == src &&
      other.tgt == tgt &&
      other.speaker == speaker &&
      other.speak == speak;

  @override
  int get hashCode => Object.hash(src, tgt, speaker, speak);

  @override
  String toString() => 'ConversationRoute($src->$tgt, $speaker, speak=$speak)';
}

class Conversation {
  const Conversation({
    this.enabled = false,
    this.side = Speaker.them,
    this.foreign = fallbackForeign,
  });

  /// The other person's language. Owner 2026-11-14: zh<->en first; ru follows once a ru ASR
  /// model and voice pack exist (SenseVoice does not recognise Russian).
  static const foreignOptions = <String, String>{'en': 'English'};
  static const fallbackForeign = 'en';

  /// The owner's language (zh-Hant subtitles; Cantonese speech also counts as "my" side).
  static const mine = 'zh';

  final bool enabled;

  /// Who is talking now; one tap switches it.
  final Speaker side;
  final String foreign;

  Conversation copyWith({bool? enabled, Speaker? side, String? foreign}) => Conversation(
    enabled: enabled ?? this.enabled,
    side: side ?? this.side,
    foreign: normalizeForeign(foreign ?? this.foreign),
  );

  /// The other side is now talking.
  Conversation switched() => copyWith(side: side == Speaker.me ? Speaker.them : Speaker.me);

  static String normalizeForeign(String? code) {
    final c = (code ?? '').trim().toLowerCase();
    return foreignOptions.containsKey(c) ? c : fallbackForeign;
  }

  /// Target for the current side (used for draft translation too).
  String targetFor(String listenTarget) {
    if (!enabled) return listenTarget;
    return side == Speaker.me ? foreign : mine;
  }

  /// Routes one finished line whose ASR-detected language is [detected]. Listen-only mode keeps
  /// the old behaviour (picked [listenTarget], nothing spoken). In conversation mode the active
  /// side picks the direction: my line -> foreign language, spoken aloud; their line -> zh, shown.
  ConversationRoute route(String detected, {required String listenTarget}) {
    if (!enabled) {
      return ConversationRoute(
        src: detected,
        tgt: listenTarget,
        speaker: Speaker.them,
        speak: false,
      );
    }
    final me = side == Speaker.me;
    return ConversationRoute(
      src: detected,
      tgt: me ? foreign : mine,
      speaker: side,
      // Nothing to say when the line is already in the target language (passthrough).
      speak: me && detected != foreign,
    );
  }

  /// Header pill text: "中文 ⇄ English".
  String get pairLabel => '中文 ⇄ ${foreignOptions[foreign]}';

  /// Side button text.
  String get sideLabel => side == Speaker.me ? '我在說（中文）' : '對方在說（${foreignOptions[foreign]}）';

  String format() => 'enabled=${enabled ? 1 : 0}\nside=${side.name}\nforeign=$foreign\n';

  static Conversation parse(String? s) {
    final m = <String, String>{};
    for (final line in (s ?? '').split('\n')) {
      final i = line.indexOf('=');
      if (i > 0) m[line.substring(0, i).trim()] = line.substring(i + 1).trim();
    }
    return Conversation(
      enabled: m['enabled'] == '1' || m['enabled'] == 'true',
      side: m['side'] == 'me' ? Speaker.me : Speaker.them,
      foreign: normalizeForeign(m['foreign']),
    );
  }

  @override
  bool operator ==(Object other) =>
      other is Conversation &&
      other.enabled == enabled &&
      other.side == side &&
      other.foreign == foreign;

  @override
  int get hashCode => Object.hash(enabled, side, foreign);
}

class ConversationStore {
  ConversationStore(this.path);
  final String path;

  Conversation load() {
    try {
      return Conversation.parse(File(path).readAsStringSync());
    } catch (_) {
      return const Conversation();
    }
  }

  void save(Conversation c) {
    try {
      File(path).writeAsStringSync(c.format());
    } catch (_) {}
  }
}
