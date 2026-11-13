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
  const MissingModelsScreen({super.key, required this.pipeline, required this.onImport});
  final Pipeline pipeline;
  final VoidCallback onImport;

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
              const Text('此 App 完全離線，不會自己下載模型。用手機瀏覽器下載模型包後，按下方按鈕匯入：',
                  style: TextStyle(fontSize: 18, color: Colors.white)),
              const SizedBox(height: 12),
              FilledButton.icon(
                icon: const Icon(Icons.download_for_offline),
                label: const Text('匯入模型 / Import models', style: TextStyle(fontSize: 20)),
                onPressed: onImport,
              ),
              const SizedBox(height: 24),
              const Text('或用電腦 adb 推送到 / Or push with adb to:',
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
                'adb push models\\mobile\\sensevoice ${p.modelsDir}/\n'
                'adb push models\\mobile\\silero_vad.onnx ${p.modelsDir}/\n'
                'adb push models\\hymt2-1.8b\\${ModelPaths.llmFile} ${p.modelsDir}/',
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
  const ErrorScreen({super.key, required this.pipeline, required this.onImport});
  final Pipeline pipeline;
  final VoidCallback onImport;

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
              if (pipeline.paths != null) ...[
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  icon: const Icon(Icons.download_for_offline),
                  label: const Text('重新匯入模型 / Re-import models'),
                  onPressed: onImport,
                ),
              ],
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
