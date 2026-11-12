import 'package:flutter/material.dart';

import '../services/model_paths.dart';
import '../services/pipeline.dart';

class LoadingScreen extends StatelessWidget {
  const LoadingScreen({super.key, required this.stage});
  final Stage stage;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(),
            const SizedBox(height: 24),
            Text(
              stage == Stage.starting ? '啟動中…' : '載入模型中（首次約 10-30 秒）…',
              style: const TextStyle(fontSize: 22, color: Colors.white70),
            ),
          ],
        ),
      ),
    );
  }
}

class MissingModelsScreen extends StatelessWidget {
  const MissingModelsScreen({super.key, required this.pipeline});
  final Pipeline pipeline;

  @override
  Widget build(BuildContext context) {
    final p = pipeline.paths!;
    const mono = TextStyle(fontFamily: 'monospace', fontSize: 14, color: Colors.white);
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: ListView(
            children: [
              const Text('找不到模型檔 / Models missing',
                  style: TextStyle(
                      fontSize: 28,
                      fontWeight: FontWeight.bold,
                      color: Colors.redAccent)),
              const SizedBox(height: 16),
              const Text('此 App 完全離線，不會下載模型。請用 adb 推送到：',
                  style: TextStyle(fontSize: 18, color: Colors.white)),
              const SizedBox(height: 8),
              SelectableText(p.modelsDir, style: mono),
              const SizedBox(height: 16),
              const Text('缺少的檔案：',
                  style: TextStyle(fontSize: 18, color: Colors.white)),
              const SizedBox(height: 8),
              for (final f in pipeline.missingFiles)
                SelectableText('• $f', style: mono.copyWith(color: Colors.amber)),
              const SizedBox(height: 16),
              const Text('PC 上執行（PowerShell）：',
                  style: TextStyle(fontSize: 18, color: Colors.white)),
              const SizedBox(height: 8),
              SelectableText(
                'adb shell mkdir -p ${p.modelsDir}\n'
                'adb push models\\mobile\\gigaam3 ${p.modelsDir}/\n'
                'adb push models\\mobile\\silero_vad.onnx ${p.modelsDir}/\n'
                'adb push models\\mobile\\${ModelPaths.llmDefault} ${p.modelsDir}/',
                style: mono,
              ),
              const SizedBox(height: 16),
              const Text('推完後完全關閉 App 再重新開啟。',
                  style: TextStyle(fontSize: 18, color: Colors.white70)),
              if (pipeline.logPath != null) ...[
                const SizedBox(height: 16),
                SelectableText('log: ${pipeline.logPath}',
                    style: mono.copyWith(color: Colors.white54)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class ErrorScreen extends StatelessWidget {
  const ErrorScreen({super.key, required this.pipeline});
  final Pipeline pipeline;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: ListView(
            children: [
              const Text('發生錯誤 / Error',
                  style: TextStyle(
                      fontSize: 28,
                      fontWeight: FontWeight.bold,
                      color: Colors.redAccent)),
              const SizedBox(height: 16),
              SelectableText(pipeline.errorText ?? 'unknown',
                  style: const TextStyle(fontSize: 18, color: Colors.white)),
              if (pipeline.logPath != null) ...[
                const SizedBox(height: 16),
                SelectableText('log: ${pipeline.logPath}',
                    style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 14,
                        color: Colors.white54)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
