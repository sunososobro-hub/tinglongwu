import 'dart:io' show Platform;

import 'package:flutter/material.dart';

import '../logic/first_use_plan.dart';
import '../logic/model_manifest.dart';
import '../logic/pack_status.dart';
import '../logic/target_language.dart';
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
class MissingModelsScreen extends StatefulWidget {
  const MissingModelsScreen({
    super.key,
    required this.download,
    required this.modelsDir,
    required this.missingFiles,
    required this.onImport,
    required this.onDone,
    this.logPath,
    this.packs = const [],
    this.baseBytes = 0,
    this.manifestLoading = false,
  });
  final BaseModelDownload download;
  final String modelsDir;
  final List<String> missingFiles;
  final VoidCallback onImport;
  final VoidCallback onDone;
  final String? logPath;

  /// The manifest's optional packs and the base zip size (#1972). With no packs the choice block
  /// is hidden and the screen is the plain one.
  final List<VoicePack> packs;
  final int baseBytes;

  /// #1998: the manifest is still being read, so the packs are not known yet: the start button is
  /// disabled (a tap would download the base only).
  final bool manifestLoading;

  @override
  State<MissingModelsScreen> createState() => _MissingModelsScreenState();
}

class _MissingModelsScreenState extends State<MissingModelsScreen> {
  static const _t = TextStyle(fontSize: 18, color: Colors.white70, height: 1.4);

  bool _conversation = false, _voice = false;
  String? _lang;

  BaseModelDownload get download => widget.download;
  bool get _hasChoice => widget.packs.isNotEmpty;

  FirstUseChoice get _choice =>
      FirstUseChoice(conversation: _conversation, voice: _voice, otherLang: _lang);

  FirstUsePlan get _plan => firstUsePlan(_choice, widget.packs, baseBytes: widget.baseBytes);

  /// Languages that have at least one optional pack.
  List<String> get _langs => {for (final p in widget.packs) p.lang}.toList()..sort();

  /// Size of one optional pack for the checkbox / chip text (manifest size).
  int? _voiceBytes() {
    final v = widget.packs.where((p) => !p.isAsr).firstOrNull;
    return v?.size;
  }

  Future<void> _start() async {
    if (_hasChoice) download.extraPacks = _plan.packs;
    if (await download.run()) widget.onDone();
  }

  Widget _choices() {
    final langs = _langs;
    final vb = _voiceBytes();
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Text('你想怎麼用？', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.white)),
      RadioGroup<bool>(
        groupValue: _conversation,
        onChanged: (v) => setState(() => _conversation = v ?? false),
        child: Column(children: [
      RadioListTile<bool>(
        key: const Key('choiceWatch'),
        value: false,
        title: const Text('看影片／聽別人說（只要字幕）'),
      ),
      RadioListTile<bool>(
        key: const Key('choiceConversation'),
        value: true,
        title: const Text('對話翻譯（我也要說話）'),
      ),
        ]),
      ),
      CheckboxListTile(
        key: const Key('choiceVoice'),
        value: _voice,
        onChanged: (v) => setState(() => _voice = v ?? false),
        title: Text(vb == null ? '朗讀語音' : '朗讀語音（+${packSize(vb)}）'),
      ),
      if (langs.isNotEmpty) ...[
        const SizedBox(height: 8),
        const Text('對方說的語言', style: _t),
        Wrap(spacing: 8, children: [
          ChoiceChip(
            key: const Key('langNone'),
            label: const Text('不需要'),
            selected: _lang == null,
            onSelected: (_) => setState(() => _lang = null),
          ),
          for (final l in langs)
            ChoiceChip(
              key: Key('lang-$l'),
              label: Text(TargetLanguage.options[l] ?? l),
              selected: _lang == l,
              onSelected: (_) => setState(() => _lang = l),
            ),
        ]),
      ],
      const SizedBox(height: 16),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final onImport = widget.onImport, modelsDir = widget.modelsDir;
    final missingFiles = widget.missingFiles, logPath = widget.logPath;
    // The total of the chosen items (#1972); the old fixed 1.3 GB text when no sizes are known.
    final size = _hasChoice && widget.baseBytes > 0 ? packSize(_plan.totalBytes) : null;
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
              Text('約 ${size ?? '1.3 GB'}，建議連 Wi-Fi。只有這次下載會連網；之後翻譯、辨識、朗讀都在手機上離線執行。',
                  style: _t),
              const SizedBox(height: 8),
              const Text('這些檔案會存在你的手機裡。之後更新 App 只會下載程式本身，不用再下載一次，所以更新很快。',
                  key: Key('whySeparateDownload'), style: _t),
              const SizedBox(height: 24),
              if (_hasChoice && !d.busy) _choices(),
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
                              ? '用行動數據下載（約 ${size ?? '1.3 GB'}）'
                              : size != null
                                  ? '開始下載（$size）'
                                  : '開始下載',
                      style: const TextStyle(fontSize: 20)),
                  onPressed: widget.manifestLoading ? null : _start,
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
