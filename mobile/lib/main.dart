import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'logic/model_manifest.dart';
import 'services/base_download.dart';
import 'services/model_importer.dart';
import 'services/pipeline.dart';
import 'ui/download_screen.dart';
import 'ui/import_screen.dart';
import 'ui/message_screen.dart';
import 'ui/pack_install_screen.dart';
import 'ui/subtitle_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
  runApp(const TlwuApp());
}

/// Whether leaving the Import screen needs a fresh pipeline: always when the app was not
/// listening (models missing / error), else only when something was imported.
bool restartAfterImport({required bool listening, required bool? imported}) =>
    !listening || imported == true;

class TlwuApp extends StatelessWidget {
  const TlwuApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '聽龍',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark(useMaterial3: true).copyWith(
        scaffoldBackgroundColor: Colors.black,
      ),
      home: const Root(),
    );
  }
}

class Root extends StatefulWidget {
  const Root({super.key});

  @override
  State<Root> createState() => _RootState();
}

class _RootState extends State<Root> {
  Pipeline pipeline = Pipeline();

  /// First-run base model download (#1863); kept across rebuilds so progress survives them.
  BaseModelDownload? _baseDl;
  List<VoicePack> _optionalPacks = const [];
  int _baseBytes = 0;

  @override
  void initState() {
    super.initState();
    pipeline.start();
  }

  /// Opens "Import models"; afterwards starts a fresh pipeline so new files are picked up. Opened
  /// from the running subtitle screen (optional packs, #1262) with nothing imported: the pipeline
  /// just keeps running, no model reload.
  Future<void> _openImport() async {
    final dir = pipeline.paths?.modelsDir;
    if (dir == null) return;
    await _pushImporter(ImportScreen(modelsDir: dir));
  }

  /// 「下載語音包」 (#1279): only the packs the chosen 對方語言 is missing.
  Future<void> _openInstall(List<VoicePack> packs) async {
    final dir = pipeline.paths?.modelsDir;
    if (dir == null) return;
    await _pushImporter(PackInstallScreen(modelsDir: dir, packs: packs));
  }

  /// #1864: on Wi-Fi the base model download starts by itself; on mobile data the screen asks.
  BaseModelDownload _newBaseDl(String dir) {
    final dl =
        BaseModelDownload(DeviceBaseModelSource(dir), url: DeviceBaseModelSource.bundleUrl);
    // #1972: with optional packs in the manifest the person picks first (one tap on 開始下載), so
    // the Wi-Fi auto start only runs when there is nothing to pick.
    () async {
      try {
        final m = await ModelImporter.loadManifest();
        final packs = m.packs;
        if (packs.isNotEmpty && mounted) {
          setState(() {
            _optionalPacks = packs;
            _baseBytes = m.files.fold(0, (a, f) => a + f.size);
          });
          return;
        }
      } catch (_) {}
      final ok = await dl.autoStart(DeviceNetworkProbe());
      if (ok && mounted) _restart();
    }();
    return dl;
  }

  /// The models are in: a fresh pipeline loads them and opens the listening screen.
  void _restart() {
    final old = pipeline;
    setState(() => pipeline = Pipeline());
    old.dispose();
    pipeline.start();
  }

  Future<void> _pushImporter(Widget screen) async {
    final listening = pipeline.stage == Stage.listening;
    final imported =
        await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => screen));
    if (!mounted || !restartAfterImport(listening: listening, imported: imported)) return;
    _restart();
  }

  @override
  void dispose() {
    _baseDl?.dispose();
    pipeline.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: pipeline,
      builder: (context, _) {
        switch (pipeline.stage) {
          case Stage.missingModels:
            final dir = pipeline.paths!.modelsDir;
            final dl = _baseDl ??= _newBaseDl(dir);
            return MissingModelsScreen(
                download: dl,
                modelsDir: dir,
                missingFiles: pipeline.missingFiles,
                logPath: pipeline.logPath,
                packs: _optionalPacks,
                baseBytes: _baseBytes,
                onImport: _openImport,
                onDone: _restart);
          case Stage.error:
            return ErrorScreen(pipeline: pipeline, onImport: _openImport);
          case Stage.downloadingModels:
            return DownloadScreen(pipeline: pipeline, onImport: _openImport);
          case Stage.starting:
          case Stage.loadingModels:
            return LoadingScreen(stage: pipeline.stage);
          case Stage.listening:
            return SubtitleScreen(
                pipeline: pipeline, onImport: _openImport, onInstall: _openInstall);
        }
      },
    );
  }
}
