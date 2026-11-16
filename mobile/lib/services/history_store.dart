import 'dart:async';
import 'dart:io';

import 'package:flutter/services.dart';

import '../logic/history.dart';

/// Local-only subtitle history files (#1311). Nothing here uploads, shares or exports: the files
/// live in the app's private folder (`<filesDir>/history/`, excluded from Android backup and
/// device transfer, and from iCloud backup on iOS).
class HistoryStore {
  HistoryStore(this.dir);
  final String dir;

  /// Sessions older than this are deleted on app start.
  static const retention = Duration(days: 30);

  static const _channel = MethodChannel('tlwu/native');

  /// The private history folder: the app's internal files dir on Android, Application Support on
  /// iOS (both private). [fallback] (the models files dir) is used when the native side is old.
  static Future<String> defaultDir(String fallback) async {
    try {
      final d = await _channel.invokeMethod<String>('privateFilesDir');
      if (d != null && d.isNotEmpty) return '$d/history';
    } catch (_) {}
    return '$fallback/history';
  }

  Directory get _d => Directory(dir);

  File _file(String id) => File('$dir/$id.jsonl');

  static String? _idOf(FileSystemEntity e) {
    final n = e.uri.pathSegments.last;
    return n.endsWith('.jsonl') ? n.substring(0, n.length - 6) : null;
  }

  /// A session id starts with its start time in ms; null for a file this app did not write.
  static int? startOfId(String id) => int.tryParse(id.split('_').first);

  /// All sessions, newest first. Unreadable files are skipped.
  Future<List<HistorySession>> list() async {
    if (!await _d.exists()) return const [];
    final out = <HistorySession>[];
    await for (final e in _d.list()) {
      final id = e is File ? _idOf(e) : null;
      if (id == null) continue;
      try {
        final s = HistoryFormat.parse(id, await (e as File).readAsString());
        if (s != null) out.add(s);
      } catch (_) {}
    }
    out.sort((a, b) => b.startMs.compareTo(a.startMs));
    return out;
  }

  Future<void> delete(String id) async {
    try {
      await _file(id).delete();
    } catch (_) {}
  }

  /// Deletes every session; returns how many files went.
  Future<int> deleteAll() async {
    var n = 0;
    if (!await _d.exists()) return 0;
    await for (final e in _d.list()) {
      if (e is File && _idOf(e) != null) {
        try {
          await e.delete();
          n++;
        } catch (_) {}
      }
    }
    return n;
  }

  /// Deletes sessions that started more than [retention] before [now]; returns how many.
  Future<int> prune(DateTime now) async {
    if (!await _d.exists()) return 0;
    final cutoff = now.subtract(retention).millisecondsSinceEpoch;
    var n = 0;
    await for (final e in _d.list()) {
      final id = e is File ? _idOf(e) : null;
      if (id == null) continue;
      final start = startOfId(id);
      if (start != null && start < cutoff) {
        try {
          await e.delete();
          n++;
        } catch (_) {}
      }
    }
    return n;
  }

  /// Starts a new session file (the header is queued at once).
  HistoryWriter open(int startMs, HistoryMode mode, String target) {
    _d.createSync(recursive: true);
    var id = '$startMs';
    for (var i = 1; _file(id).existsSync(); i++) {
      id = '${startMs}_$i';
    }
    _file(id).createSync(); // reserves the name (a same-millisecond session gets the next one)
    return HistoryWriter._(_file(id), id, startMs, mode, target);
  }
}

/// Appends one session. Every write is a separate async append (flushed), chained so the order
/// holds; the caller never waits, so audio and translation are not blocked, and a crash loses
/// at most the line being written.
class HistoryWriter {
  HistoryWriter._(this._file, this.id, int startMs, HistoryMode mode, String target) {
    _append(HistoryFormat.header(startMs, mode, target), header: true);
  }

  final File _file;
  final String id;
  Future<void> _chain = Future.value();
  bool _closed = false;

  bool _gone = false;

  void _append(String s, {bool header = false}) {
    _chain = _chain.then((_) async {
      // The file was deleted while lines were still queued: do not bring it back.
      if (_gone || (!header && !await _file.exists())) {
        _gone = true;
        return;
      }
      await _file.writeAsString(s, mode: FileMode.append, flush: true);
    }).catchError((Object _) {
      // disk full or folder gone: history is best-effort, never breaks the translation
    });
  }

  void add(HistoryLine l) {
    if (!_closed) _append(HistoryFormat.line(l));
  }

  /// Writes the end record. Completes when everything is on disk.
  Future<void> close(int endMs) {
    if (!_closed) {
      _closed = true;
      _append(HistoryFormat.end(endMs));
    }
    return _chain;
  }

  /// Completes when everything queued so far is on disk.
  Future<void> get done => _chain;
}

/// What the pipeline talks to: opens a session file lazily on its first line (so a session
/// without any finalized line leaves no file), honours the on/off switch, and starts a fresh
/// session after a delete. Draft text is never passed in (only finalized lines are added).
class HistoryRecorder {
  HistoryRecorder(this.store, {required this.enabled, int Function()? nowMs})
      : _nowMs = nowMs ?? (() => DateTime.now().millisecondsSinceEpoch);

  final HistoryStore store;
  bool enabled;
  final int Function() _nowMs;

  HistoryMode? _mode;
  String _target = 'zh';
  int _startMs = 0;
  HistoryWriter? _w;

  /// Closed sessions whose last writes may still be queued; a delete waits for them.
  final Map<String, HistoryWriter> _closing = {};

  /// The session being written, null before its first line.
  String? get activeId => _w?.id;

  /// Ends the running session (if any) and starts a new one (written on its first line).
  void begin(HistoryMode mode, String target) {
    _closeWriter();
    _mode = mode;
    _target = target;
    _startMs = _nowMs();
  }

  /// The pipeline's current mode: a change ends the session and starts the next one. Same mode =
  /// no-op (a language change inside a mode does not split the session; every line has its own).
  void setMode(HistoryMode mode, String target) {
    if (mode != _mode) begin(mode, target);
  }

  void setEnabled(bool on) {
    if (on == enabled) return;
    enabled = on;
    if (!on) {
      _closeWriter();
    } else if (_mode != null) {
      _startMs = _nowMs(); // a fresh session from now on
    }
  }

  /// One finalized line. Cheap and non-blocking: the write is queued.
  void add({required String src, required String tr, String? lang, String? tgt, int? side}) {
    final mode = _mode;
    if (!enabled || mode == null) return;
    if (src.trim().isEmpty && tr.trim().isEmpty) return;
    try {
      _w ??= store.open(_startMs, mode, _target);
      _w!.add(HistoryLine(atMs: _nowMs(), src: src, tr: tr, lang: lang, tgt: tgt, side: side));
    } catch (_) {
      _w = null; // folder not writable: skip, try again on the next line
    }
  }

  /// Deletes one session; when it is the running one, the next line starts a new session.
  Future<void> delete(String id) async {
    if (_w?.id == id) {
      final w = _w!;
      _w = null;
      _startMs = _nowMs();
      await w.close(_nowMs());
    }
    await _closing[id]?.done;
    await store.delete(id);
  }

  Future<int> deleteAll() async {
    final w = _w;
    _w = null;
    _startMs = _nowMs();
    await w?.close(_nowMs());
    await Future.wait([for (final c in _closing.values.toList()) c.done]);
    return store.deleteAll();
  }

  /// Ends the running session; completes when it is on disk.
  Future<void> end() async {
    final w = _w;
    _w = null;
    await w?.close(_nowMs());
  }

  void _closeWriter() {
    final w = _w;
    _w = null;
    if (w == null) return;
    _closing[w.id] = w;
    w.close(_nowMs()).whenComplete(() => _closing.remove(w.id));
  }
}
