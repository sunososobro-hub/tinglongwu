import 'dart:io';

import 'package:flutter/services.dart';

/// Where side-loaded models live and how to check them.
///
/// Layout under `<externalFilesDir>/models/`:
///   sherpa-ru/encoder.int8.onnx, decoder.onnx, joiner.int8.onnx, tokens.txt
///   Qwen3-1.7B-Q4_K_M.gguf
class ModelPaths {
  ModelPaths(this.filesDir);

  final String filesDir;

  static const packageName = 'com.tinglongwu.mobile';
  static const _channel = MethodChannel('tlwu/native');

  static const asrDirName = 'sherpa-ru';
  static const asrFiles = [
    'encoder.int8.onnx',
    'decoder.onnx',
    'joiner.int8.onnx',
    'tokens.txt',
  ];
  static const llmFileName = 'Qwen3-1.7B-Q4_K_M.gguf';

  static Future<ModelPaths> resolve() async {
    final dir = await _channel.invokeMethod<String>('externalFilesDir');
    return ModelPaths(dir!);
  }

  String get modelsDir => '$filesDir/models';
  String get asrDir => '$modelsDir/$asrDirName';
  String get llmPath => '$modelsDir/$llmFileName';
  String asrFile(String name) => '$asrDir/$name';

  /// Returns the list of missing files (empty = all present).
  List<String> missing() {
    final out = <String>[];
    for (final f in asrFiles) {
      if (!File(asrFile(f)).existsSync()) out.add(asrFile(f));
    }
    if (!File(llmPath).existsSync()) out.add(llmPath);
    return out;
  }
}
