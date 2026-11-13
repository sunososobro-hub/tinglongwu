import 'dart:io';

import 'package:flutter/services.dart';

/// Where side-loaded models live and how to check them.
///
/// Layout under `<externalFilesDir>/models/`:
///   sensevoice/model.int8.onnx, tokens.txt (SenseVoice-small, zh/en/ja/ko/yue)
///   silero_vad.onnx
///   Hy-MT2-1.8B-Q4_K_M.gguf (translator, same file as desktop)
class ModelPaths {
  ModelPaths(this.filesDir);

  final String filesDir;

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
    return ModelPaths(dir!);
  }

  String get modelsDir => '$filesDir/models';
  String get asrDir => '$modelsDir/$asrDirName';
  String get vadPath => '$modelsDir/$vadFileName';

  /// Translator GGUF. Fixed file: the prompt is Hy-MT2's template, so no
  /// other model can be swapped in.
  String get llmPath => '$modelsDir/$llmFile';

  String asrFile(String name) => '$asrDir/$name';

  /// Returns the list of missing files (empty = all present).
  List<String> missing() {
    final out = <String>[];
    for (final f in asrFiles) {
      if (!File(asrFile(f)).existsSync()) out.add(asrFile(f));
    }
    if (!File(vadPath).existsSync()) out.add(vadPath);
    if (!File(llmPath).existsSync()) out.add(llmPath);
    return out;
  }
}
