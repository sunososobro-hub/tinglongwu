import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'logic/model_manifest.dart';
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

  Future<void> _pushImporter(Widget screen) async {
    final listening = pipeline.stage == Stage.listening;
    final imported =
        await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => screen));
    if (!mounted || !restartAfterImport(listening: listening, imported: imported)) return;
    final old = pipeline;
    setState(() => pipeline = Pipeline());
    old.dispose();
    pipeline.start();
  }

  @override
  void dispose() {
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
            return MissingModelsScreen(pipeline: pipeline, onImport: _openImport);
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
