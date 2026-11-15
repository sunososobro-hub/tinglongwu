import 'package:flutter/material.dart';

import '../logic/pack_download.dart';
import '../services/pipeline.dart';
import '../services/play_packs.dart';

/// First run of a Play install (#1031): Play is still downloading the fast-follow model packs.
/// Plain zh-TW, progress bar + MB, Wi-Fi wait, failure + retry. Nothing to import.
class DownloadScreen extends StatelessWidget {
  const DownloadScreen({super.key, required this.pipeline, required this.onImport});
  final Pipeline pipeline;
  final VoidCallback onImport;

  static const _t = TextStyle(fontSize: 18, color: Colors.white70, height: 1.4);

  @override
  Widget build(BuildContext context) {
    final v = PackDownloadView.of(
        done: pipeline.dlDone,
        total: pipeline.dlTotal,
        state: pipeline.dlState,
        error: pipeline.dlError);
    return Scaffold(
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(24), children: [
          const SizedBox(height: 32),
          const Text(PackDownloadView.title,
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold, color: Colors.white)),
          const SizedBox(height: 24),
          if (!v.retry) ...[
            LinearProgressIndicator(value: v.progress, minHeight: 10),
            const SizedBox(height: 8),
          ],
          if (v.amount.isNotEmpty)
            Text(v.amount, style: const TextStyle(fontSize: 20, color: Colors.white)),
          const SizedBox(height: 16),
          Text(v.status,
              style: TextStyle(
                  fontSize: 20, color: v.retry ? Colors.amberAccent : Colors.white)),
          if (v.hint.isNotEmpty) ...[const SizedBox(height: 8), Text(v.hint, style: _t)],
          const SizedBox(height: 24),
          if (v.retry)
            FilledButton.icon(
              icon: const Icon(Icons.refresh),
              label: const Text('重試', style: TextStyle(fontSize: 20)),
              onPressed: pipeline.start,
            ),
          if (v.mobileData)
            OutlinedButton.icon(
              icon: const Icon(Icons.signal_cellular_alt),
              label: const Text('改用行動數據下載', style: TextStyle(fontSize: 18)),
              onPressed: PlayPacks.confirmMobileData,
            ),
          if (v.retry) ...[
            const SizedBox(height: 32),
            TextButton(onPressed: onImport, child: const Text('改用手動匯入模型')),
          ],
        ]),
      ),
    );
  }
}
