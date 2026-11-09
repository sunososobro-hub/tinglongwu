import 'dart:io';

/// Append-only log file in the app's external files dir:
/// `<filesDir>/logs/tlwu.log` (read later with `adb pull`).
class AppLog {
  AppLog._(this._sink, this.path);

  final IOSink _sink;
  final String path;
  static AppLog? _instance;
  static const _maxBytes = 5 * 1024 * 1024;

  static AppLog get instance =>
      _instance ?? (throw StateError('AppLog.init() not called'));

  static Future<AppLog> init(String filesDir) async {
    final dir = Directory('$filesDir/logs');
    await dir.create(recursive: true);
    final f = File('${dir.path}/tlwu.log');
    if (await f.exists() && await f.length() > _maxBytes) {
      final old = File('${dir.path}/tlwu.log.1');
      if (await old.exists()) await old.delete();
      await f.rename(old.path);
    }
    final sink = File('${dir.path}/tlwu.log').openWrite(mode: FileMode.append);
    final log = AppLog._(sink, '${dir.path}/tlwu.log');
    _instance = log;
    log.i('=== session start ${DateTime.now().toIso8601String()} ===');
    return log;
  }

  void _w(String lvl, String msg) {
    final t = DateTime.now().toIso8601String();
    _sink.writeln('$t $lvl $msg');
  }

  void i(String msg) => _w('I', msg);
  void e(String msg) => _w('E', msg);

  Future<void> flush() => _sink.flush();
}
