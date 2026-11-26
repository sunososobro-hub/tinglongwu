import 'dart:io' show Platform;

import 'package:flutter/material.dart';

import '../services/base_download.dart';
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

/// First open with the base models missing (#1863): on Wi-Fi the download has already started by
/// itself (#1864, [BaseModelDownload.autoStart]); on mobile data one tap on
/// 「用行動數據下載（約 1.3 GB）」 starts it. Size + SHA-256 of every file is checked, then [onDone]
/// starts the listening screen. Importing stays a
/// secondary option; the adb / PowerShell / file-path details are folded away.
class MissingModelsScreen extends StatelessWidget {
  const MissingModelsScreen({
    super.key,
    required this.download,
    required this.modelsDir,
    required this.missingFiles,
    required this.onImport,
    required this.onDone,
    this.logPath,
  });
  final BaseModelDownload download;
  final String modelsDir;
  final List<String> missingFiles;
  final VoidCallback onImport;
  final VoidCallback onDone;
  final String? logPath;

  static const _t = TextStyle(fontSize: 18, color: Colors.white70, height: 1.4);

  Future<void> _start() async {
    if (await download.run()) onDone();
  }

  @override
  Widget build(BuildContext context) {
    const mono = TextStyle(fontFamily: 'monospace', fontSize: 14, color: Colors.white);
    return Scaffold(
      body: SafeArea(
        child: ListenableBuilder(
          listenable: download,
          builder: (context, _) {
            final d = download;
            return ListView(padding: const EdgeInsets.all(24), children: [
              const SizedBox(height: 24),
              const Text('第一次使用：下載翻譯需要的檔案',
                  style: TextStyle(fontSize: 26, fontWeight: FontWeight.bold, color: Colors.white)),
              const SizedBox(height: 12),
              const Text('約 1.3 GB，建議連 Wi-Fi。只有這次下載會連網；之後翻譯、辨識、朗讀都在手機上離線執行。',
                  style: _t),
              const SizedBox(height: 8),
              const Text('這些檔案會存在你的手機裡。之後更新 App 只會下載程式本身，不用再下載一次，所以更新很快。',
                  key: Key('whySeparateDownload'), style: _t),
              const SizedBox(height: 24),
              if (d.busy) ...[
                LinearProgressIndicator(value: d.progress, minHeight: 10),
                const SizedBox(height: 8),
                Text(d.state == BaseDlState.checking ? '正在檢查並安裝檔案…' : '下載中… ${d.amount}',
                    style: const TextStyle(fontSize: 20, color: Colors.white)),
                const SizedBox(height: 16),
                if (d.state == BaseDlState.downloading)
                  OutlinedButton(onPressed: d.cancel, child: const Text('取消')),
              ] else ...[
                if (d.error != null) ...[
                  Text(d.error!, style: const TextStyle(fontSize: 18, color: Colors.amberAccent)),
                  const SizedBox(height: 16),
                ],
                if (d.onMobileData && d.error == null) ...[
                  const Text('目前是行動數據，所以還沒開始下載。連上 Wi-Fi 後重開 App 會自動下載。', style: _t),
                  const SizedBox(height: 12),
                ],
                FilledButton.icon(
                  icon: Icon(d.error == null ? Icons.download : Icons.refresh),
                  label: Text(
                      d.error != null
                          ? '重試'
                          : d.onMobileData
                              ? '用行動數據下載（約 1.3 GB）'
                              : '開始下載',
                      style: const TextStyle(fontSize: 20)),
                  onPressed: _start,
                ),
              ],
              const SizedBox(height: 24),
              TextButton(onPressed: d.busy ? null : onImport, child: const Text('改用匯入模型')),
              ExpansionTile(
                title: const Text('顯示技術細節', style: TextStyle(color: Colors.white54)),
                children: [
                  if (Platform.isAndroid) ...[
                    const Text('adb 推送到：', style: _t),
                    SelectableText(modelsDir, style: mono),
                    const SizedBox(height: 12),
                    const Text('PC 上執行（PowerShell）：', style: _t),
                    SelectableText(
                      'adb shell mkdir -p $modelsDir\n'
                      'adb push models\\mobile\\sensevoice $modelsDir/\n'
                      'adb push models\\mobile\\silero_vad.onnx $modelsDir/\n'
                      'adb push models\\hymt2-1.8b\\${ModelPaths.llmFile} $modelsDir/',
                      style: mono,
                    ),
                    const SizedBox(height: 12),
                  ],
                  const Text('缺少的檔案：', style: _t),
                  for (final f in missingFiles)
                    SelectableText('• $f', style: mono.copyWith(color: Colors.amber)),
                  if (logPath != null)
                    SelectableText('log: $logPath', style: mono.copyWith(color: Colors.white54)),
                ],
              ),
            ]);
          },
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
