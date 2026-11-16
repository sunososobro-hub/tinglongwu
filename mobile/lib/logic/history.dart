import 'dart:convert';

/// Local-only subtitle history (#1311): the data model and the JSONL format. Pure Dart.
///
/// One session = one file `startMs.jsonl`, appended line by line as the subtitles finalize:
///   {"v":1,"t":"s","start":ms,"mode":"speak|conversation|video","tgt":"zh"}   header (first)
///   {"t":"l","at":ms,"src":"...","tr":"...","lang":"ru","tgt":"zh","side":0}  one finalized line
///   {"t":"e","end":ms}                                                         written on close
/// A session that was killed has no `e` record; its end is the last line's time. A half-written
/// last line (crash) is skipped. Draft text and audio are never part of the format.

/// How the session was run.
enum HistoryMode {
  /// 說話翻譯: the microphone.
  speak,

  /// 對話翻譯: two people, each line has a speaker side.
  conversation,

  /// 看影片翻譯: audio of other apps, subtitles on the overlay.
  video;

  static HistoryMode parse(Object? s) =>
      HistoryMode.values.firstWhere((m) => m.name == s, orElse: () => HistoryMode.speak);

  String get label => switch (this) {
        HistoryMode.speak => '說話翻譯',
        HistoryMode.conversation => '對話翻譯',
        HistoryMode.video => '看影片翻譯',
      };
}

/// One finalized subtitle: original [src] and translation [tr] (either may be empty, not both).
class HistoryLine {
  const HistoryLine(
      {required this.atMs,
      required this.src,
      required this.tr,
      this.lang,
      this.tgt,
      this.side});

  final int atMs;
  final String src;
  final String tr;

  /// Detected source language code (ru, en, ...), null when unknown.
  final String? lang;

  /// Language the translation is in (zh = zh-Hant), null when unknown.
  final String? tgt;

  /// Conversation mode: 0 = this phone's owner, 1 = the other person. Null in the other modes.
  final int? side;

  Map<String, Object?> toJson() => {
        't': 'l',
        'at': atMs,
        'src': src,
        'tr': tr,
        if (lang != null) 'lang': lang,
        if (tgt != null) 'tgt': tgt,
        if (side != null) 'side': side,
      };

  static HistoryLine? fromJson(Map<String, Object?> j) {
    final at = j['at'];
    final src = j['src'];
    final tr = j['tr'];
    if (at is! int || src is! String || tr is! String) return null;
    final lang = j['lang'];
    final tgt = j['tgt'];
    final side = j['side'];
    return HistoryLine(
      atMs: at,
      src: src,
      tr: tr,
      lang: lang is String && lang.isNotEmpty ? lang : null,
      tgt: tgt is String && tgt.isNotEmpty ? tgt : null,
      side: side is int ? side : null,
    );
  }
}

class HistorySession {
  const HistorySession({
    required this.id,
    required this.startMs,
    required this.endMs,
    required this.mode,
    required this.target,
    required this.lines,
  });

  /// The file name without extension (= [startMs], plus a suffix on a clash).
  final String id;
  final int startMs;
  final int endMs;
  final HistoryMode mode;

  /// Subtitle (target) language code of the first line, e.g. zh = zh-Hant.
  final String target;
  final List<HistoryLine> lines;

  DateTime get start => DateTime.fromMillisecondsSinceEpoch(startMs);

  /// Source languages heard, in order of first appearance.
  List<String> get sourceLangs {
    final seen = <String>[];
    for (final l in lines) {
      final c = l.lang;
      if (c != null && !seen.contains(c)) seen.add(c);
    }
    return seen;
  }

  /// First line for the list: the translation, else the original.
  String get preview {
    for (final l in lines) {
      final t = l.tr.trim().isNotEmpty ? l.tr.trim() : l.src.trim();
      if (t.isNotEmpty) return t;
    }
    return '';
  }

  static const _names = {
    'zh': '中文',
    'yue': '粵語',
    'en': '英語',
    'ja': '日語',
    'ko': '韓語',
    'ru': '俄語',
    'es': '西班牙語',
    'fr': '法語',
    'de': '德語',
    'th': '泰語',
    'vi': '越南語',
    'id': '印尼語',
  };

  static String _name(String c) => _names[c] ?? c;

  /// Languages the translations are in, in order of first appearance (header target if no line
  /// says).
  List<String> get targetLangs {
    final seen = <String>[];
    for (final l in lines) {
      final c = l.tgt;
      if (c != null && !seen.contains(c)) seen.add(c);
    }
    return seen.isEmpty ? [target] : seen;
  }

  /// "俄語 → 中文" (listening modes) or "中文 ⇄ 英語" (conversation), from the lines.
  String get languageLabel {
    final src = sourceLangs;
    final tgt = targetLangs;
    if (mode == HistoryMode.conversation) {
      final all = <String>[];
      for (final c in [...tgt, ...src]) {
        final f = c == 'yue' ? 'zh' : c;
        if (!all.contains(f)) all.add(f);
      }
      return all.map(_name).join(' ⇄ ');
    }
    final s = src.map(_name).join('、');
    final t = tgt.map(_name).join('、');
    return s.isEmpty ? '→ $t' : '$s → $t';
  }
}

class HistoryFormat {
  static const version = 1;

  static String header(int startMs, HistoryMode mode, String target) =>
      '${jsonEncode({'v': version, 't': 's', 'start': startMs, 'mode': mode.name, 'tgt': target})}\n';

  static String line(HistoryLine l) => '${jsonEncode(l.toJson())}\n';

  static String end(int endMs) => '${jsonEncode({'t': 'e', 'end': endMs})}\n';

  /// Parses a whole file. Returns null when there is no valid header. Bad lines are skipped.
  static HistorySession? parse(String id, String text) {
    int? start;
    var mode = HistoryMode.speak;
    var target = 'zh';
    int? end;
    final lines = <HistoryLine>[];
    for (final raw in const LineSplitter().convert(text)) {
      if (raw.trim().isEmpty) continue;
      final Object? j;
      try {
        j = jsonDecode(raw);
      } catch (_) {
        continue; // truncated by a crash
      }
      if (j is! Map<String, Object?>) continue;
      switch (j['t']) {
        case 's':
          if (start == null && j['start'] is int) {
            start = j['start'] as int;
            mode = HistoryMode.parse(j['mode']);
            if (j['tgt'] is String) target = j['tgt'] as String;
          }
        case 'l':
          final l = HistoryLine.fromJson(j);
          if (l != null) lines.add(l);
        case 'e':
          if (j['end'] is int) end = j['end'] as int;
      }
    }
    if (start == null) return null;
    return HistorySession(
      id: id,
      startMs: start,
      endMs: end ?? (lines.isEmpty ? start : lines.last.atMs),
      mode: mode,
      target: target,
      lines: lines,
    );
  }
}
