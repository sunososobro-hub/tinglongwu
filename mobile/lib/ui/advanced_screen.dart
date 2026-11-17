import 'package:flutter/material.dart';

import '../logic/device_probe.dart';
import '../services/pipeline.dart';
import 'licences_screen.dart';
import 'version_label.dart';

/// 進階功能 (#1065): optional features with a recommendation from this phone's speed test.
/// The user decides; nothing is turned on automatically.
class AdvancedScreen extends StatelessWidget {
  const AdvancedScreen({super.key, required this.pipeline, this.onImport});
  final Pipeline pipeline;

  /// Opens "Import models" (#1262), for the optional speaker model.
  final VoidCallback? onImport;

  static Color _color(AdviceBadge b) => switch (b) {
        AdviceBadge.recommend => Colors.greenAccent,
        AdviceBadge.tryIt => Colors.amber,
        AdviceBadge.avoid => Colors.redAccent,
        AdviceBadge.unknown => Colors.white54,
      };

  Widget _feature({
    required String title,
    required String what,
    required Advice advice,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    final c = _color(advice.badge);
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: Row(children: [
            Flexible(child: Text(title, style: const TextStyle(fontSize: 17))),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                  border: Border.all(color: c), borderRadius: BorderRadius.circular(10)),
              child: Text(advice.badge.label, style: TextStyle(fontSize: 12, color: c)),
            ),
          ]),
          subtitle: Text(what, style: const TextStyle(fontSize: 13, color: Colors.white60)),
          value: value,
          onChanged: onChanged,
        ),
        Text(advice.reason, style: TextStyle(fontSize: 13, color: c.withValues(alpha: 0.9))),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('進階功能')),
      body: ListenableBuilder(
        listenable: pipeline,
        builder: (context, _) {
          final probe = pipeline.probe;
          final info = probe?.info;
          final bench = probe?.bench;
          return ListView(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
            children: [
              const Text('這些功能會多用一些耗電和記憶體。建議是依你手機的測速結果給的，要不要開由你決定。',
                  style: TextStyle(fontSize: 13, color: Colors.white60)),
              const SizedBox(height: 16),
              _feature(
                title: '顯示原文草稿',
                what: '一句話還沒說完時，先用灰字顯示聽到的原文，說完後換成正式字幕。',
                advice: adviseSourceDraft(info ?? const DeviceInfo(cores: 1), bench),
                value: pipeline.prefs.sourceDraft,
                onChanged: pipeline.setSourceDraft,
              ),
              _feature(
                title: '中文草稿',
                what: '把灰字草稿也先翻成中文（最多每 2 秒一次，正式字幕優先）。',
                advice: adviseZhDraft(info ?? const DeviceInfo(cores: 1), bench,
                    sourceDraftOn: pipeline.prefs.sourceDraft),
                value: pipeline.prefs.zhDraft,
                onChanged: pipeline.setZhDraft,
              ),
              _feature(
                title: '說話者標籤',
                what: '依聲音分辨是誰在說話，字幕泡泡依說話者換顏色並標上「說話者 N」。'
                    '需要另外匯入 28 MB 的模型；聲音只在手機上比對，不會存檔。',
                advice: adviseSpeakerLabels(info ?? const DeviceInfo(cores: 1), bench,
                    modelInstalled: pipeline.paths?.hasSpeakerModel ?? false),
                value: pipeline.prefs.speakerLabels,
                onChanged: (on) {
                  if (!pipeline.setSpeakerLabels(on) && on) {
                    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                        content: const Text('還沒有說話者模型：請先匯入 28 MB 的選用檔案。'),
                        action: onImport == null
                            ? null
                            : SnackBarAction(
                                label: '匯入模型',
                                onPressed: () {
                                  Navigator.of(context).pop();
                                  onImport!();
                                })));
                  }
                },
              ),
              const Divider(color: Colors.white12, height: 32),
              const Text('你的手機', style: TextStyle(fontSize: 16)),
              const SizedBox(height: 6),
              Text(info?.summary() ?? '讀取中…',
                  style: const TextStyle(fontSize: 13, color: Colors.white70)),
              if (bench != null) ...[
                const SizedBox(height: 4),
                Text(
                    '語音辨識約 ${bench.asrSpeedup.toStringAsFixed(1)} 倍即時・'
                    '翻譯約 ${bench.llmCharsPerSec.toStringAsFixed(1)} 字/秒'
                    '（${bench.llmTokensPerSec.toStringAsFixed(1)} tok/s）',
                    style: const TextStyle(fontSize: 13, color: Colors.white70)),
              ],
              const SizedBox(height: 12),
              Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton(
                  onPressed: pipeline.benchRunning ? null : () => pipeline.runBenchmark(),
                  child: Text(pipeline.benchRunning ? '測速中…' : '重新測速'),
                ),
              ),
              const SizedBox(height: 4),
              const Text('測速約幾秒，會暫停字幕一下。請在沒人說話時進行。',
                  style: TextStyle(fontSize: 12, color: Colors.white38)),
              const SizedBox(height: 32),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Open-source licences'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => Navigator.of(context)
                    .push(MaterialPageRoute(builder: (_) => const LicencesScreen())),
              ),
              const VersionLabel(),
            ],
          );
        },
      ),
    );
  }
}
