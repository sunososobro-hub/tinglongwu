import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'services/pipeline.dart';
import 'ui/message_screen.dart';
import 'ui/subtitle_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
  runApp(const TlwuApp());
}

class TlwuApp extends StatelessWidget {
  const TlwuApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '聽攏有',
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
  final Pipeline pipeline = Pipeline();

  @override
  void initState() {
    super.initState();
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
            return MissingModelsScreen(pipeline: pipeline);
          case Stage.error:
            return ErrorScreen(pipeline: pipeline);
          case Stage.starting:
          case Stage.loadingModels:
            return LoadingScreen(stage: pipeline.stage);
          case Stage.listening:
            return SubtitleScreen(pipeline: pipeline);
        }
      },
    );
  }
}
