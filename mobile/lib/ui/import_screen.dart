import 'package:flutter/material.dart';

import '../logic/model_manifest.dart';
import '../services/model_importer.dart';
import '../services/model_paths.dart';
import '../services/play_packs.dart';

/// "Import models": pick the models zip (or single files) with the system file picker and copy
/// them into the app's models dir, checking size and SHA-256 against the manifest.
/// Pops with `true` when something was imported.
class ImportScreen extends StatefulWidget {
  const ImportScreen({super.key, required this.modelsDir, this.manifest});
  final String modelsDir;

  /// Tests: use this manifest instead of the bundled assets (and no Play pack folders).
  final ModelManifest? manifest;

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
  bool _play = false; // installed from Google Play: models come as asset packs
  bool _playRun = false; // the running job is a Play download (cancel goes to Play first)

  @override
  void initState() {
    super.initState();
    PlayPacks.available().then((v) {
      if (mounted) setState(() => _play = v);
    });
    (widget.manifest != null ? Future.value(widget.manifest!) : ModelImporter.loadManifest())
        .then((m) async {
      final dirs = widget.manifest != null ? const <String>[] : await ModelPaths.packLocations();
      if (!mounted) return;
      setState(() {
        _imp = ModelImporter(m, widget.modelsDir);
        _present = _imp!.presentFiles(packDirs: dirs);
      });
    }, onError: (Object e) {
      if (mounted) setState(() => _loadError = '$e');
    });
  }

  Future<void> _pickAndImport() async {
    final imp = _imp!;
    final docs = await ModelImporter.pick();
    if (docs.isEmpty || !mounted) return;
    await _runJob(() => imp.run(docs, onProgress: _onProgress));
  }

  Future<void> _playImport() async {
    _playRun = true;
    await _runJob(() => PlayPacks.download(onProgress: _onProgress));
    _playRun = false;
  }

  void _onProgress(int d, int t, String n) {
    if (mounted) {
      setState(() {
        _done = d;
        _total = t;
        _name = n;
      });
    }
  }

  Future<void> _runJob(Future<ImportReport> Function() job) async {
    final imp = _imp!;
    setState(() {
      _busy = true;
      _report = null;
      _done = 0;
      _total = 0;
    });
    ImportReport r;
    try {
      r = await job();
    } catch (e) {
      r = ImportReport()..problems.add('$e');
    }
    final dirs = await ModelPaths.packLocations();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _report = r;
      _importedAny |= r.imported.isNotEmpty;
      _present = imp.presentFiles(packDirs: dirs);
    });
  }

  void _cancel() {
    if (_playRun) PlayPacks.cancel();
    ModelImporter.cancel();
  }

  List<Widget> _playSection() => [
        const Text('從 Google Play 下載 / Download from Google Play', style: _h),
        const SizedBox(height: 6),
        const Text('安裝後 Play 商店會自動下載模型（約 1.4 GB，建議用 Wi-Fi）；沒下載完可以按這裡繼續。'
            '檔案直接在 Play 放的位置使用，不另外複製。本 app 仍然沒有網路權限。', style: _t),
        const SizedBox(height: 12),
        if (!_busy)
          FilledButton.icon(
            icon: const Icon(Icons.download),
            label: const Text('從 Play 下載模型 / Get models', style: TextStyle(fontSize: 18)),
            onPressed: _playImport,
          ),
        const SizedBox(height: 20),
        const Text('或手動匯入 / Or import manually', style: _h),
        const SizedBox(height: 12),
      ];

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
    return PopScope<bool>(
      // The system back gesture must report what the back arrow reports (#1262): the caller
      // restarts the pipeline only when something was imported.
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && !_busy) Navigator.of(context).pop(_importedAny);
      },
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
    final voices = m.installedPacks(_present);
    // Base models already here (#1262): the screen is about the optional packs, so it does not
    // ask for the 1.4 GB bundle (or the Play download) again.
    final needBase = missing.isNotEmpty;
    String got(bool installed) => installed ? '✓ 已安裝。' : '';
    return [
      if (_play && needBase) ..._playSection(),
      Text(needBase ? '1. 下載模型包 / Download the models' : '1. 下載選用套件 / Download optional packs',
          style: _h),
      const SizedBox(height: 6),
      if (needBase) ...[
        Text('用手機瀏覽器下載 ${m.bundleName}（存到「下載」）。${m.bundleNote}', style: _t),
        _link('下載模型包 / Download ${m.bundleName}', m.bundleUrl),
      ] else
        const Text('基本模型已安裝。下面是選用套件：用手機瀏覽器下載需要的 zip（存到「下載」），再到第 2 步匯入。',
            key: Key('optionalIntro'), style: _t),
      for (final f in big) ...[
        const SizedBox(height: 6),
        Text('選用：較大較準的 ${f.basename}（${(f.size / (1 << 30)).toStringAsFixed(1)} GB，官方連結）。',
            style: _t),
        _link('下載 / Download ${f.basename}', f.url!),
      ],
      for (final p in m.packs) ...[
        const SizedBox(height: 6),
        Text(
            got(voices.contains(p.id)) +
            (p.isAsr
                ? '選用：對話模式聽懂對方的語言（${p.lang}）${p.name}（${_fmt(p.size)}），'
                    '裝了才能辨識對方說的${p.lang}。${p.note}'
                : '選用：對話模式朗讀語音（${p.lang}）${p.name}（${_fmt(p.size)}），'
                    '裝了就會把我說的話的翻譯念出來。${p.note}'),
            style: _t),
        _link('下載 / Download ${p.name}', p.url),
      ],
      if (m.speakerModel != null) ...[
        const SizedBox(height: 6),
        Text('${got(m.speakerModelInstalled(_present))}選用：說話者標籤模型 ${m.speakerModel!.basename}（${_fmt(m.speakerModel!.size)}），'
            '裝了才能在「進階功能」開啟。${m.speakerNote}', style: _t),
        _link('下載 / Download ${m.speakerModel!.basename}', m.speakerModel!.url!),
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
        OutlinedButton(onPressed: _cancel, child: const Text('取消 / Cancel')),
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
      for (final p in m.packs)
        Text('${voices.contains(p.id) ? '✓' : '·'} ${p.folder}/ （選用 / optional）',
            style: _mono.copyWith(
                color: voices.contains(p.id) ? Colors.greenAccent : Colors.white38)),
      if (m.speakerModel != null)
        Text('${m.speakerModelInstalled(_present) ? '✓' : '·'} speaker/ （選用 / optional）',
            style: _mono.copyWith(
                color: m.speakerModelInstalled(_present) ? Colors.greenAccent : Colors.white38)),
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
