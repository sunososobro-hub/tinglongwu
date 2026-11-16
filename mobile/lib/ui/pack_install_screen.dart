import 'package:flutter/material.dart';

import '../logic/model_manifest.dart';
import '../logic/pack_status.dart';
import '../logic/target_language.dart';
import '../services/model_importer.dart';
import '../services/model_paths.dart';

/// What the screen needs from the phone; the tests replace it.
abstract class PackInstaller {
  /// Starts the browser download of [url]; false when no app can open it.
  Future<bool> openUrl(String url);

  /// The system file picker, opened in the Downloads folder.
  Future<List<PickedDoc>> pick();

  /// Imports the picked files (size + SHA-256 checked against the manifest).
  Future<ImportReport> run(List<PickedDoc> docs,
      {required void Function(int done, int total, String name) onProgress});

  Future<void> cancel();

  /// Manifest paths that are on the phone now.
  Future<Set<String>> present();
}

/// The real one: browser + Storage Access Framework + [ModelImporter].
class DevicePackInstaller implements PackInstaller {
  DevicePackInstaller(this.importer);
  final ModelImporter importer;

  @override
  Future<bool> openUrl(String url) => ModelImporter.openUrl(url);

  @override
  Future<List<PickedDoc>> pick() => ModelImporter.pick(downloads: true);

  @override
  Future<ImportReport> run(List<PickedDoc> docs,
          {required void Function(int done, int total, String name) onProgress}) =>
      importer.run(docs, onProgress: onProgress);

  @override
  Future<void> cancel() => ModelImporter.cancel();

  @override
  Future<Set<String>> present() async =>
      importer.presentFiles(packDirs: await ModelPaths.packLocations());
}

/// "English 朗讀語音包" / "Русский 辨識包".
String packTitle(VoicePack p) =>
    '${TargetLanguage.options[p.lang] ?? p.lang} ${p.isAsr ? '辨識包' : '朗讀語音包'}';

/// 「下載語音包」 (#1279): only the packs the chosen 對方語言 is missing. The app has no INTERNET
/// permission, so the phone's browser downloads each zip (one tap per pack, never automatic) and
/// one more tap imports them through the usual size + SHA-256 check. Pops with `true` when
/// something was imported; closes by itself once every listed pack is installed.
class PackInstallScreen extends StatefulWidget {
  const PackInstallScreen({super.key, required this.modelsDir, required this.packs, this.installer});
  final String modelsDir;
  final List<VoicePack> packs;

  /// Tests: a fake instead of the browser, the file picker and the importer.
  final PackInstaller? installer;

  @override
  State<PackInstallScreen> createState() => _PackInstallScreenState();
}

class _PackInstallScreenState extends State<PackInstallScreen> {
  PackInstaller? _ins;
  String? _loadError;
  Set<String> _present = {};
  final Set<String> _started = {}; // pack ids whose browser download was started
  bool _busy = false;
  int _done = 0, _total = 0;
  String _name = '';
  ImportReport? _report;
  bool _importedAny = false;
  String? _openError;

  static const _h = TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white);
  static const _t = TextStyle(fontSize: 15, color: Colors.white70, height: 1.4);

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final ins = widget.installer ??
          DevicePackInstaller(ModelImporter(await ModelImporter.loadManifest(), widget.modelsDir));
      final present = await ins.present();
      if (!mounted) return;
      setState(() {
        _ins = ins;
        _present = present;
      });
    } catch (e) {
      if (mounted) setState(() => _loadError = '$e');
    }
  }

  bool _installed(VoicePack p) => p.files.every((f) => _present.contains(f.path));
  bool get _allInstalled => widget.packs.every(_installed);

  Future<void> _download(VoicePack p) async {
    final ok = await _ins!.openUrl(p.url);
    if (!mounted) return;
    setState(() {
      _openError = ok ? null : '打不開瀏覽器。請用手機瀏覽器開這個網址：${p.url}';
      if (ok) _started.add(p.id);
    });
  }

  Future<void> _import() async {
    final ins = _ins!;
    final docs = await ins.pick();
    if (docs.isEmpty || !mounted) return;
    setState(() {
      _busy = true;
      _report = null;
      _done = 0;
      _total = 0;
    });
    ImportReport r;
    try {
      r = await ins.run(docs, onProgress: (d, t, n) {
        if (mounted) {
          setState(() {
            _done = d;
            _total = t;
            _name = n;
          });
        }
      });
    } catch (e) {
      r = ImportReport()..problems.add('$e');
    }
    final present = await ins.present();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _report = r;
      _importedAny |= r.imported.isNotEmpty;
      _present = present;
    });
    // Everything this language needs is here: back to the conversation, the voice loads.
    if (_allInstalled && _importedAny) Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final onlyVoice = widget.packs.every((p) => !p.isAsr);
    return PopScope<bool>(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && !_busy) Navigator.of(context).pop(_importedAny);
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(onlyVoice ? '下載語音包' : '下載辨識包和語音包'),
          leading: _busy
              ? null
              : BackButton(onPressed: () => Navigator.of(context).pop(_importedAny)),
        ),
        body: SafeArea(
          child: _loadError != null
              ? Center(child: Text('manifest error: $_loadError', style: _t))
              : _ins == null
                  ? const Center(child: CircularProgressIndicator())
                  : ListView(padding: const EdgeInsets.all(20), children: _body()),
        ),
      ),
    );
  }

  List<Widget> _body() {
    final missing = widget.packs.where((p) => !_installed(p)).toList();
    final bytes = missing.fold<int>(0, (a, p) => a + p.size);
    return [
      const Text('聽龍本身不連網路，所以由手機的瀏覽器幫你下載，再回來匯入。按了按鈕才會下載。', style: _t),
      const SizedBox(height: 16),
      const Text('1. 下載', style: _h),
      const SizedBox(height: 8),
      for (final p in widget.packs) _packCard(p),
      if (missing.isNotEmpty) ...[
        const SizedBox(height: 4),
        Text('建議使用 Wi-Fi：用行動數據會用掉約 ${packSize(bytes)}。下載進度看瀏覽器或通知列。',
            key: const Key('dataWarning'), style: _t.copyWith(color: Colors.amber)),
      ],
      if (_openError != null) ...[
        const SizedBox(height: 8),
        SelectableText(_openError!, style: _t.copyWith(color: Colors.redAccent)),
      ],
      const SizedBox(height: 20),
      const Text('2. 匯入', style: _h),
      const SizedBox(height: 6),
      Text(
          '下載完成後按下面的按鈕，在「下載」裡點選 '
          '${[for (final p in missing.isEmpty ? widget.packs : missing) p.name].join('、')}'
          '${missing.length > 1 ? '（可以一次選兩個）' : ''}。檔案會核對大小與 SHA-256，不符就不會使用。',
          style: _t),
      const SizedBox(height: 12),
      if (_busy) ...[
        LinearProgressIndicator(value: _total > 0 ? (_done / _total).clamp(0.0, 1.0) : null),
        const SizedBox(height: 8),
        Text('$_name  ${packSize(_done)} / ${_total > 0 ? packSize(_total) : '?'}', style: _t),
        const SizedBox(height: 8),
        OutlinedButton(
            key: const Key('cancelImport'), onPressed: _ins!.cancel, child: const Text('取消')),
      ] else if (_allInstalled)
        FilledButton.icon(
          key: const Key('installDone'),
          icon: const Icon(Icons.check),
          label: const Text('已安裝，回到對話', style: TextStyle(fontSize: 17)),
          onPressed: () => Navigator.of(context).pop(_importedAny),
        )
      else
        FilledButton.icon(
          key: const Key('importDownloaded'),
          icon: const Icon(Icons.folder_open),
          label: const Text('匯入下載好的檔案', style: TextStyle(fontSize: 17)),
          onPressed: _import,
        ),
      if (_report != null) ...[
        const SizedBox(height: 12),
        if (_report!.cancelled) Text('已取消', style: _t.copyWith(color: Colors.amber)),
        if (_report!.imported.isNotEmpty && !_allInstalled)
          Text('已匯入一部分，還缺：${[for (final p in missing) packTitle(p)].join('、')}',
              style: _t.copyWith(color: Colors.amber)),
        for (final p in _report!.problems)
          Text('✗ $p', style: _t.copyWith(color: Colors.redAccent)),
      ],
    ];
  }

  Widget _packCard(VoicePack p) {
    final done = _installed(p);
    return Container(
      key: Key('pack:${p.id}'),
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Text(packTitle(p), style: const TextStyle(fontSize: 16))),
          Text(packSize(p.size), style: _t),
        ]),
        Text(p.name, style: const TextStyle(fontSize: 12, color: Colors.white38)),
        const SizedBox(height: 6),
        if (done)
          const Row(children: [
            Icon(Icons.check_circle, size: 18, color: Colors.greenAccent),
            SizedBox(width: 6),
            Text('已安裝', style: TextStyle(fontSize: 15, color: Colors.greenAccent)),
          ])
        else ...[
          OutlinedButton.icon(
            key: Key('download:${p.id}'),
            icon: const Icon(Icons.download),
            label: Text('用瀏覽器下載 (${packSize(p.size)})'),
            onPressed: _busy ? null : () => _download(p),
          ),
          if (_started.contains(p.id))
            const Text('已交給瀏覽器下載。下載完回到這裡做第 2 步。',
                style: TextStyle(fontSize: 13, color: Colors.white60)),
        ],
      ]),
    );
  }
}
