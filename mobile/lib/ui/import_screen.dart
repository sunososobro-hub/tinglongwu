import 'package:flutter/material.dart';

import '../services/model_importer.dart';

/// "Import models": pick the models zip (or single files) with the system file picker and copy
/// them into the app's models dir, checking size and SHA-256 against the manifest.
/// Pops with `true` when something was imported.
class ImportScreen extends StatefulWidget {
  const ImportScreen({super.key, required this.modelsDir});
  final String modelsDir;

  @override
  State<ImportScreen> createState() => _ImportScreenState();
}

class _ImportScreenState extends State<ImportScreen> {
  ModelImporter? _imp;
  String? _loadError;
  Set<String> _present = {};
  bool _busy = false;
  int _done = 0, _total = 0;
  String _name = '';
  ImportReport? _report;
  bool _importedAny = false;

  @override
  void initState() {
    super.initState();
    ModelImporter.loadManifest().then((m) {
      if (!mounted) return;
      setState(() {
        _imp = ModelImporter(m, widget.modelsDir);
        _present = _imp!.presentFiles();
      });
    }, onError: (Object e) {
      if (mounted) setState(() => _loadError = '$e');
    });
  }

  Future<void> _pickAndImport() async {
    final imp = _imp!;
    final docs = await ModelImporter.pick();
    if (docs.isEmpty || !mounted) return;
    setState(() {
      _busy = true;
      _report = null;
      _done = 0;
      _total = 0;
    });
    final r = await imp.run(docs, onProgress: (d, t, n) {
      if (mounted) {
        setState(() {
          _done = d;
          _total = t;
          _name = n;
        });
      }
    });
    if (!mounted) return;
    setState(() {
      _busy = false;
      _report = r;
      _importedAny |= r.imported.isNotEmpty;
      _present = imp.presentFiles();
    });
  }

  static const _h = TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: Colors.white);
  static const _t = TextStyle(fontSize: 16, color: Colors.white70, height: 1.4);
  static const _mono = TextStyle(fontFamily: 'monospace', fontSize: 13, color: Colors.white);

  Widget _link(String label, String url) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          OutlinedButton.icon(
            icon: const Icon(Icons.open_in_browser),
            label: Text(label),
            onPressed: () => ModelImporter.openUrl(url),
          ),
          SelectableText(url, style: _mono.copyWith(color: Colors.white54, fontSize: 12)),
        ]),
      );

  @override
  Widget build(BuildContext context) {
    final imp = _imp;
    return PopScope(
      canPop: !_busy,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('匯入模型 / Import models'),
          leading: _busy
              ? null
              : BackButton(onPressed: () => Navigator.of(context).pop(_importedAny)),
        ),
        body: SafeArea(
          child: _loadError != null
              ? Center(child: Text('manifest error: $_loadError', style: _t))
              : imp == null
                  ? const Center(child: CircularProgressIndicator())
                  : ListView(padding: const EdgeInsets.all(20), children: _body(imp)),
        ),
      ),
    );
  }

  List<Widget> _body(ModelImporter imp) {
    final m = imp.manifest;
    final missing = m.missingRequired(_present);
    final big = m.files.where((f) => f.url != null).toList();
    return [
      const Text('1. 下載模型包 / Download the models', style: _h),
      const SizedBox(height: 6),
      Text('用手機瀏覽器下載 ${m.bundleName}（存到「下載」）。${m.bundleNote}', style: _t),
      _link('下載模型包 / Download ${m.bundleName}', m.bundleUrl),
      for (final f in big) ...[
        const SizedBox(height: 6),
        Text('選用：較大較準的 ${f.basename}（${(f.size / (1 << 30)).toStringAsFixed(1)} GB，官方連結）。',
            style: _t),
        _link('下載 / Download ${f.basename}', f.url!),
      ],
      const SizedBox(height: 20),
      const Text('2. 匯入 / Import', style: _h),
      const SizedBox(height: 6),
      const Text('按下方按鈕，在「下載」裡選 zip（也可一次選多個 .onnx / .gguf / .txt 檔）。'
          '每個檔案都會核對大小與 SHA-256，不符就不會使用。', style: _t),
      const SizedBox(height: 12),
      if (_busy) ...[
        LinearProgressIndicator(value: _total > 0 ? (_done / _total).clamp(0.0, 1.0) : null),
        const SizedBox(height: 8),
        Text('$_name  ${_fmt(_done)} / ${_total > 0 ? _fmt(_total) : '?'}', style: _t),
        const SizedBox(height: 8),
        OutlinedButton(onPressed: ModelImporter.cancel, child: const Text('取消 / Cancel')),
      ] else
        FilledButton.icon(
          icon: const Icon(Icons.folder_open),
          label: const Text('選擇檔案 / Pick files', style: TextStyle(fontSize: 18)),
          onPressed: _pickAndImport,
        ),
      if (_report != null) ..._reportWidgets(_report!),
      const SizedBox(height: 20),
      const Text('狀態 / Status', style: _h),
      const SizedBox(height: 6),
      for (final f in m.files)
        Text('${_present.contains(f.path) ? '✓' : '·'} ${f.path}',
            style: _mono.copyWith(
                color: _present.contains(f.path) ? Colors.greenAccent : Colors.white38)),
      const SizedBox(height: 12),
      if (missing.isEmpty && !_busy)
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('完成，開始使用 / Done, start', style: TextStyle(fontSize: 18)),
        )
      else
        Text('還缺 / Still missing: ${missing.join(', ')}',
            style: _t.copyWith(color: Colors.amber)),
      const SizedBox(height: 12),
      SelectableText('→ ${imp.modelsDir}', style: _mono.copyWith(color: Colors.white38)),
    ];
  }

  List<Widget> _reportWidgets(ImportReport r) => [
        const SizedBox(height: 12),
        if (r.cancelled) Text('已取消 / Cancelled', style: _t.copyWith(color: Colors.amber)),
        if (r.imported.isNotEmpty)
          Text('已匯入 / Imported: ${r.imported.join(', ')}',
              style: _t.copyWith(color: Colors.greenAccent)),
        for (final p in r.problems) Text('✗ $p', style: _t.copyWith(color: Colors.redAccent)),
      ];

  static String _fmt(int b) => b >= (1 << 30)
      ? '${(b / (1 << 30)).toStringAsFixed(2)} GB'
      : '${(b / (1 << 20)).toStringAsFixed(0)} MB';
}
