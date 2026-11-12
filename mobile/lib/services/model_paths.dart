import 'dart:io';

import 'package:flutter/services.dart';

/// Where side-loaded models live and how to check them.
///
/// Layout under `<externalFilesDir>/models/`:
///   gigaam3/encoder.int8.onnx, decoder.onnx, joiner.onnx, tokens.txt
///   silero_vad.onnx
///   Qwen3-4B-Q4_K_M.gguf (default) / Qwen3-1.7B-Q4_K_M.gguf (fallback)
///   llm.txt (optional: one line with the gguf file name to use instead)
class ModelPaths {
  ModelPaths(this.filesDir);

  final String filesDir;

  static const packageName = 'com.tinglongwu.mobile';
  static const _channel = MethodChannel('tlwu/native');

  static const asrDirName = 'gigaam3';
  static const asrFiles = [
    'encoder.int8.onnx',
    'decoder.onnx',
    'joiner.onnx',
    'tokens.txt',
  ];
  static const vadFileName = 'silero_vad.onnx';
  static const llmDefault = 'Qwen3-4B-Q4_K_M.gguf';
  static const llmFallback = 'Qwen3-1.7B-Q4_K_M.gguf';

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

  /// LLM to load: llm.txt override if present, else 4B, else the 1.7B fallback.
  String get llmPath {
    try {
      final f = File('$modelsDir/llm.txt');
      if (f.existsSync()) {
        final n = f.readAsStringSync().trim();
        if (n.isNotEmpty && File('$modelsDir/$n').existsSync()) {
          return '$modelsDir/$n';
        }
      }
    } catch (_) {}
    if (File('$modelsDir/$llmDefault').existsSync()) {
      return '$modelsDir/$llmDefault';
    }
    return '$modelsDir/$llmFallback';
  }
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
