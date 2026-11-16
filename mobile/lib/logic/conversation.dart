import 'dart:io';

import 'target_language.dart';

/// Face-to-face conversation mode (#558, part 1): the owner speaks Chinese and the phone shows
/// (later: speaks) the translation in the other person's language; the other person speaks their
/// language and the owner reads zh-Hant. Pure logic + a small text file, testable without Flutter.
///
/// Off by default: the app stays listen-only (every line -> the picked subtitle language).
enum Speaker { me, them }

/// Language family for matching ASR output against a user language: Cantonese counts as zh.
String langFamily(String code) => code == 'yue' ? 'zh' : code;

/// Two-way direction (pure): a line spoken in [mine] (yue counts as zh) goes to [partner];
/// anything else goes to [mine].
({String src, String tgt}) twoWayDirection(String detected,
        {required String mine, required String partner}) =>
    (src: detected, tgt: langFamily(detected) == langFamily(mine) ? partner : mine);

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
    this.mineLang = 'zh',
    this.auto = true,
  });

  /// The other person's language. Owner 2026-11-14: zh<->en first; ru follows once a ru ASR
  /// model and voice pack exist (SenseVoice does not recognise Russian).
  static const foreignOptions = TargetLanguage.options;
  static const fallbackForeign = 'en';

  /// The owner's language (zh-Hant subtitles; Cantonese speech also counts as "my" side).
  static const mine = 'zh';

  /// My language (default zh); one of [TargetLanguage.spoken].
  final String mineLang;

  /// Pick each line's direction from the ASR-detected language (my language -> [foreign], else
  /// -> my language). Off = the manual [side] button decides.
  final bool auto;

  final bool enabled;

  /// Who is talking now; one tap switches it.
  final Speaker side;
  final String foreign;

  Conversation copyWith(
      {bool? enabled, Speaker? side, String? foreign, String? mineLang, bool? auto}) {
    final m = normalizeMine(mineLang ?? this.mineLang);
    var f = normalizeForeign(foreign ?? this.foreign);
    if (f == m) f = m == fallbackForeign ? 'zh' : fallbackForeign; // never my own language
    return Conversation(
      enabled: enabled ?? this.enabled,
      side: side ?? this.side,
      foreign: f,
      mineLang: m,
      auto: auto ?? this.auto,
    );
  }

  static String normalizeMine(String? code) {
    final c = (code ?? '').trim().toLowerCase();
    return TargetLanguage.spoken.contains(c) ? c : 'zh';
  }

  /// The other side is now talking.
  Conversation switched() => copyWith(side: side == Speaker.me ? Speaker.them : Speaker.me);

  static String normalizeForeign(String? code) {
    final c = (code ?? '').trim().toLowerCase();
    return foreignOptions.containsKey(c) ? c : fallbackForeign;
  }

  /// Target for the current side (used for draft translation too).
  String targetFor(String listenTarget) {
    if (!enabled) return listenTarget;
    return side == Speaker.me ? foreign : mineLang;
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
    if (auto) {
      final d = twoWayDirection(detected, mine: mineLang, partner: foreign);
      final mineLine = d.tgt == foreign;
      return ConversationRoute(
        src: detected,
        tgt: d.tgt,
        speaker: mineLine ? Speaker.me : Speaker.them,
        speak: mineLine && detected != foreign,
      );
    }
    final me = side == Speaker.me;
    return ConversationRoute(
      src: detected,
      tgt: me ? foreign : mineLang,
      speaker: side,
      // Nothing to say when the line is already in the target language (passthrough).
      speak: me && detected != foreign,
    );
  }

  /// Header pill text: "中文 ⇄ English".
  String get pairLabel => '${_name(mineLang)} ⇄ ${foreignOptions[foreign]}';

  static String _name(String c) => c == 'zh' ? '中文' : (foreignOptions[c] ?? c);

  /// Side button text.
  String get sideLabel => side == Speaker.me ? '我在說（${_name(mineLang)}）' : '對方在說（${foreignOptions[foreign]}）';

  String format() => 'enabled=${enabled ? 1 : 0}\nside=${side.name}\nforeign=$foreign\nmine=$mineLang\nauto=${auto ? 1 : 0}\n';

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
      mineLang: normalizeMine(m['mine']),
      auto: m['auto'] != '0' && m['auto'] != 'false',
    ).copyWith();
  }

  @override
  bool operator ==(Object other) =>
      other is Conversation &&
      other.enabled == enabled &&
      other.side == side &&
      other.foreign == foreign &&
      other.mineLang == mineLang &&
      other.auto == auto;

  @override
  int get hashCode => Object.hash(enabled, side, foreign, mineLang, auto);
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
