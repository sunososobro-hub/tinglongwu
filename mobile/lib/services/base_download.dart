import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../logic/model_manifest.dart';
import 'model_importer.dart';
import 'model_paths.dart';
import 'pack_downloader.dart';

/// What the first-run base model download (#1863) needs from the phone; the tests replace it.
abstract class BaseModelSource {
  /// Downloads [url] (the manifest's models zip) and returns the saved file path. Throws
  /// [PlatformException] (CANCELLED, BAD_URL, BUSY, DOWNLOAD_FAILED).
  Future<String> download(String url, void Function(int done, int total) onProgress,
      {bool unmeteredOnly = false});

  /// Installs every model file of the zip at [path], each checked against the manifest's size and
  /// SHA-256 (a file that does not match is not installed and is listed in `problems`).
  Future<ImportReport> install(String path);

  /// Downloads the optional pack [p] (its zip, from this repo's release assets) and returns the
  /// saved file path. Throws [PlatformException] like [download].
  Future<String> downloadPack(VoicePack p, void Function(int done, int total) onProgress,
      {bool unmeteredOnly = false});

  /// Installs the pack zip at [path] for [p], each file checked against its size and SHA-256.
  Future<ImportReport> installPack(VoicePack p, String path);

  /// Every file of [p] is already on the phone (a retry skips it).
  Future<bool> packInstalled(VoicePack p);

  /// Required model files still missing after the install.
  Future<List<String>> missing();

  Future<void> cancel();
}

enum BaseDlState { idle, downloading, checking, failed, done }

/// What kind of network the phone is on (#1864). Only [unmetered] (Wi-Fi) starts the base model
/// download by itself; [metered] (mobile data) waits for 「用行動數據下載」.
enum NetKind { unmetered, metered, none, unknown }

/// Asks the phone which [NetKind] it is on; the tests replace it.
abstract class NetworkProbe {
  Future<NetKind> kind();
}

/// Decides the [NetKind] from NetworkProbe.kt's raw facts (#1864). [NetKind.unmetered] needs an
/// unmetered Wi-Fi or Ethernet transport with no cellular transport: Android can mark 5G cellular
/// NOT_METERED, and that must still ask before ~1.3 GB of mobile data. Null or malformed -> unknown.
NetKind netKindFrom(Object? facts) {
  if (facts is! Map) return NetKind.unknown;
  bool f(String k) => facts[k] == true;
  if (!f('internet')) return NetKind.none;
  if (f('notMetered') && (f('wifi') || f('ethernet')) && !f('cellular')) return NetKind.unmetered;
  return NetKind.metered;
}

/// The real one: NetworkProbe.kt (ConnectivityManager, ACCESS_NETWORK_STATE; opens no connection).
class DeviceNetworkProbe implements NetworkProbe {
  static const _ch = MethodChannel('tlwu/native');

  @override
  Future<NetKind> kind() async {
    try {
      return netKindFrom(await _ch.invokeMethod<Object?>('networkFacts'));
    } catch (_) {
      return NetKind.unknown;
    }
  }
}

/// Download -> check -> install of the base models (SenseVoice, Silero VAD, Hy-MT2). [run] returns
/// true only when nothing is missing afterwards; a checksum mismatch or a cancel leaves it
/// [BaseDlState.failed] with a zh-TW [error] and [run] can be called again (重試).
class BaseModelDownload extends ChangeNotifier {
  BaseModelDownload(this.src, {required this.url, this.extraPacks = const []});
  final BaseModelSource src;
  final String url;

  /// Optional packs chosen on the first-use screen (#1971), fetched after the base zip in the same
  /// run. Empty = the base zip only.
  List<VoicePack> extraPacks;

  BaseDlState state = BaseDlState.idle;
  int done = 0, total = 0;
  String? error;

  /// The network seen by [autoStart]; null until it has asked.
  NetKind? net;
  bool _autoTried = false;

  bool get busy => state == BaseDlState.downloading || state == BaseDlState.checking;

  /// On mobile data the start button names the cost (#1864).
  bool get onMobileData => net == NetKind.metered;

  /// First open (#1864): on an unmetered network (Wi-Fi) the download starts with no tap and this
  /// returns [run]'s result; on mobile data, no network or an unknown one nothing is fetched and it
  /// returns false (the screen then waits for a tap). Only the first call does anything.
  Future<bool> autoStart(NetworkProbe probe) async {
    if (_autoTried || busy || state == BaseDlState.done) return false;
    _autoTried = true;
    final k = await _probe(probe);
    if (k != NetKind.unmetered) return false;
    return run(unmeteredOnly: true);
  }

  /// Only look at the network (#1998): sets [net] so the screen can name the mobile-data cost, but
  /// never starts a download. For the first-use screen with optional packs, where the person taps.
  Future<void> probeNetwork(NetworkProbe probe) async {
    if (_autoTried || busy || state == BaseDlState.done) return;
    _autoTried = true;
    await _probe(probe);
  }

  Future<NetKind> _probe(NetworkProbe probe) async {
    NetKind k;
    try {
      k = await probe.kind();
    } catch (_) {
      k = NetKind.unknown;
    }
    net = k;
    notifyListeners();
    return k;
  }

  /// "123 / 1310 MB", empty while the total is not known.
  String get amount => total > 0 ? '${_mb(done)} / ${_mb(total)} MB' : '';

  /// 0..1, or null for an indeterminate bar.
  double? get progress => total > 0 ? (done / total).clamp(0.0, 1.0) : null;

  /// [unmeteredOnly] (#1879, set by [autoStart]): every connection stays on the unmetered network.
  Future<bool> run({bool unmeteredOnly = false}) async {
    if (busy) return false;
    _set(BaseDlState.downloading, null);
    done = total = 0;
    final packBytes = extraPacks.fold<int>(0, (a, p) => a + p.size);
    try {
      var baseTotal = 0;
      final path = await src.download(url, (d, t) {
        baseTotal = t;
        done = d;
        total = t + packBytes;
        notifyListeners();
      }, unmeteredOnly: unmeteredOnly);
      var offset = baseTotal; // bytes of the items finished before the current one
      _set(BaseDlState.checking, null);
      final r = await src.install(path);
      if (r.cancelled) return _fail('下載已停止。');
      if (r.problems.isNotEmpty) {
        return _fail('檔案檢查不符，沒有安裝：\n${r.problems.join('\n')}');
      }
      for (final p in extraPacks) {
        if (await src.packInstalled(p)) {
          offset += p.size;
          done = offset;
          continue;
        }
        _set(BaseDlState.downloading, null);
        final pp = await src.downloadPack(p, (d, t) {
          done = offset + d;
          notifyListeners();
        }, unmeteredOnly: unmeteredOnly);
        _set(BaseDlState.checking, null);
        final pr = await src.installPack(p, pp);
        if (pr.cancelled) return _fail('下載已停止。');
        if (pr.problems.isNotEmpty) {
          return _fail('檔案檢查不符，沒有安裝：\n${pr.problems.join('\n')}');
        }
        offset += p.size;
        done = offset;
      }
      final miss = await src.missing();
      if (miss.isNotEmpty) return _fail('下載完成，但還缺少：\n${miss.join('\n')}');
      _set(BaseDlState.done, null);
      return true;
    } on PlatformException catch (e) {
      return _fail(e.code == 'CANCELLED' ? '下載已停止。' : '下載失敗：${e.message ?? e.code}');
    } catch (e) {
      return _fail('下載失敗：$e');
    }
  }

  Future<void> cancel() => src.cancel();

  bool _fail(String msg) {
    _set(BaseDlState.failed, msg);
    return false;
  }

  void _set(BaseDlState s, String? err) {
    state = s;
    error = err;
    notifyListeners();
  }

  static String _mb(int b) => (b / (1 << 20)).toStringAsFixed(0);
}

/// The real one: [PackDownloader] (this repo's release assets only) + [ModelImporter] (size +
/// SHA-256 of every file against assets/models_manifest.json). The zip's own hash is not in the
/// manifest, so the transfer itself is not checked; every file inside is.
class DeviceBaseModelSource implements BaseModelSource {
  DeviceBaseModelSource(this.modelsDir);
  final String modelsDir;

  static const zipName = 'tlwu-mobile-models.zip';

  /// Same as `bundle.url` in assets/models_manifest.json (test/base_download_test.dart checks it).
  static const bundleUrl =
      'https://github.com/sunososobro-hub/tinglongwu/releases/download/mobile-latest/$zipName';

  @override
  Future<String> download(String url, void Function(int done, int total) onProgress,
          {bool unmeteredOnly = false}) =>
      PackDownloader.download(url, '$modelsDir/.dl/$zipName',
          size: -1, sha256: '', onProgress: onProgress, unmeteredOnly: unmeteredOnly);

  @override
  Future<ImportReport> install(String path) async {
    final f = File(path);
    try {
      final importer = ModelImporter(await ModelImporter.loadManifest(), modelsDir);
      return await importer.run([PickedDoc(Uri.file(path).toString(), zipName, f.lengthSync())],
          onProgress: (_, _, _) {});
    } finally {
      try {
        if (f.existsSync()) f.deleteSync(); // 1.3 GB: never keep the zip once its files are in
      } catch (_) {}
    }
  }

  @override
  Future<String> downloadPack(VoicePack p, void Function(int done, int total) onProgress,
          {bool unmeteredOnly = false}) =>
      PackDownloader.download(p.url, '$modelsDir/.dl/${p.name}',
          size: -1, sha256: '', onProgress: onProgress, unmeteredOnly: unmeteredOnly);

  @override
  Future<ImportReport> installPack(VoicePack p, String path) async {
    final f = File(path);
    try {
      final importer = ModelImporter(await ModelImporter.loadManifest(), modelsDir);
      return await importer.run([PickedDoc(Uri.file(path).toString(), p.name, f.lengthSync())],
          onProgress: (_, _, _) {});
    } finally {
      try {
        if (f.existsSync()) f.deleteSync();
      } catch (_) {}
    }
  }

  @override
  Future<bool> packInstalled(VoicePack p) async {
    final importer = ModelImporter(await ModelImporter.loadManifest(), modelsDir);
    final present = importer.presentFiles(packDirs: await ModelPaths.packLocations());
    return p.files.every((f) => present.contains(f.path));
  }

  @override
  Future<List<String>> missing() async => (await ModelPaths.resolve()).missing();

  @override
  Future<void> cancel() async {
    try {
      await PackDownloader.cancel();
    } catch (_) {}
    await ModelImporter.cancel();
  }
}
