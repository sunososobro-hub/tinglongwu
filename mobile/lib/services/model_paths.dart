import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';

import '../logic/lang_pack.dart';
import '../logic/speech_out.dart';

/// Where the models live and how to check them.
///
/// Each file is taken from `<externalFilesDir>/models/` when it is there (imported, adb-pushed or
/// copied from the old on-demand packs; never replaced or deleted), else straight from a Google
/// Play fast-follow pack folder ([packDirs], read in place, no copy) when the size matches the
/// manifest. Same relative layout in both:
///   sensevoice/model.int8.onnx, tokens.txt (SenseVoice-small, zh/en/ja/ko/yue)
///   silero_vad.onnx
///   Hy-MT2-1.8B-Q4_K_M.gguf (translator, same file as desktop)
class ModelPaths {
  ModelPaths(this.filesDir, {this.packDirs = const [], this.sizes = const {}});

  final String filesDir;

  /// Play asset pack folders (`AssetPackLocation.assetsPath()`); empty for a side-loaded APK.
  /// Asked again on every start: Play may move them when it updates a pack.
  final List<String> packDirs;

  /// Manifest size per relative path; a pack file with another size is not used.
  final Map<String, int> sizes;

  static const packageName = 'com.tinglongwu.mobile';
  static const _channel = MethodChannel('tlwu/native');

  static const asrDirName = 'sensevoice';
  static const asrFiles = [
    'model.int8.onnx',
    'tokens.txt',
  ];
  static const vadFileName = 'silero_vad.onnx';
  /// Tencent Hy-MT2-1.8B Q4_K_M GGUF (~1.1 GB, Apache-2.0), the file desktop
  /// `tlwu-setup --hymt` puts in models/hymt2-1.8b/.
  static const llmFile = 'Hy-MT2-1.8B-Q4_K_M.gguf';

  /// "temp=38.5C bat=80% thermal=0" (cheap; for log lines).
  static Future<String> statsLine() async {
    try {
      final m = await _channel.invokeMapMethod<String, dynamic>('stats');
      return 'temp=${m?['tempC']}C bat=${m?['level']}% thermal=${m?['thermal']}';
    } catch (_) {
      return 'temp=n/a';
    }
  }

  String get testWavDir => '$filesDir/testwav';

  static Future<ModelPaths> resolve() async {
    final dir = await _channel.invokeMethod<String>('externalFilesDir');
    return ModelPaths(dir!, packDirs: await packLocations(), sizes: await _manifestSizes());
  }

  /// Folders of the Play packs already on the device (none for a side-loaded APK or iOS).
  static Future<List<String>> packLocations() async {
    try {
      final m = await _channel.invokeMapMethod<String, String>('playLocations');
      return m == null ? const [] : (m.keys.toList()..sort()).map((k) => m[k]!).toList();
    } catch (_) {
      return const []; // no Play / old native side
    }
  }

  static Future<Map<String, int>> _manifestSizes() async {
    try {
      final j = jsonDecode(await rootBundle.loadString('assets/models_manifest.json')) as Map;
      return {
        for (final f in j['files'] as List) (f as Map)['path'] as String: (f['size'] as num).toInt()
      };
    } catch (_) {
      return const {};
    }
  }

  String get modelsDir => '$filesDir/models';

  /// Where the file at [rel] (manifest path) is read from: the models dir when the file is there,
  /// else a pack folder with a file of the manifest size, else the models dir (= missing).
  String path(String rel) {
    final local = '$modelsDir/$rel';
    if (File(local).existsSync()) return local;
    for (final d in packDirs) {
      final f = File('$d/$rel');
      try {
        if (f.existsSync() && (sizes[rel] == null || f.lengthSync() == sizes[rel])) return f.path;
      } catch (_) {}
    }
    return local;
  }

  String get vadPath => path(vadFileName);

  /// Translator GGUF. Fixed file: the prompt is Hy-MT2's template, so no
  /// other model can be swapped in.
  String get llmPath => path(llmFile);

  String asrFile(String name) => path('$asrDirName/$name');

  /// Optional speaker-embedding model (#1155); the file may be missing (labels stay off).
  static const speakerRel = 'speaker/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx';
  String get speakerPath => path(speakerRel);
  bool get hasSpeakerModel => File(speakerPath).existsSync();

  /// Optional read-aloud voice for [lang] (#558); null when not installed (text only).
  TtsVoicePaths? ttsVoice(String lang) =>
      TtsVoicePaths.find(lang, resolve: path, exists: (p) => File(p).existsSync());

  /// Optional extra-language recognizer for [lang] (#556, Russian); null when not installed.
  AsrPackPaths? asrPack(String lang) =>
      AsrPackPaths.find(lang, resolve: path, exists: (p) => File(p).existsSync());

  /// Returns the list of missing files (empty = all present).
  List<String> missing() => [
        for (final p in [for (final f in asrFiles) asrFile(f), vadPath, llmPath])
          if (!File(p).existsSync()) p,
      ];
}
