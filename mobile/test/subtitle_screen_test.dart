import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/pipeline.dart';
import 'package:mobile/ui/subtitle_screen.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  // Pipeline() creates a `record` AudioRecorder; stub its platform channel.
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('com.llfbandit.record/messages'), (call) async => null);

  Pipeline make() {
    final p = Pipeline();
    p.stage = Stage.listening;
    return p;
  }

  testWidgets('empty state shows hint, idle/listening status and mic button', (t) async {
    final p = make();
    await t.pumpWidget(MaterialApp(home: SubtitleScreen(pipeline: p)));
    expect(find.text(emptyHint(phone: false)), findsOneWidget);
    expect(find.text('聆聽中'), findsOneWidget);
    expect(find.byKey(const Key('micButton')), findsOneWidget);
    expect(listenStateOf(p), ListenState.on);
    p.paused = true;
    expect(listenStateOf(p), ListenState.idle);
    p.switchingSource = true;
    expect(listenStateOf(p), ListenState.loading);
    p.stage = Stage.error;
    expect(listenStateOf(p), ListenState.error);
  });

  testWidgets('bubbles render source small and translation; screenshot', (t) async {
    final p = make();
    p.subtitles
      ..add(Subtitle('Where is the train station?', zh: '火車站在哪裡？'))
      ..add(Subtitle('How long does it take?', zh: '要走多久？'));
    p.draftRu = "It's about five…";
    await t.binding.setSurfaceSize(const Size(390, 844));
    final fontFile = Platform.environment['TLWU_FONT'];
    if (fontFile != null) {
      final l = FontLoader('Roboto')..addFont(Future.value(ByteData.sublistView(File(fontFile).readAsBytesSync())));
      await l.load();
    }
    final key = GlobalKey();
    await t.pumpWidget(RepaintBoundary(
        key: key,
        child: MaterialApp(
            theme: ThemeData.dark(useMaterial3: true),
            home: SubtitleScreen(pipeline: p))));
    expect(find.text('火車站在哪裡？'), findsOneWidget);
    expect(find.text('要走多久？'), findsOneWidget);
    expect(find.text('自動辨識'), findsOneWidget);
    final out = Platform.environment['TLWU_SHOT'];
    if (out != null) {
      await t.runAsync(() async {
        final img = await (key.currentContext!.findRenderObject() as RenderRepaintBoundary)
            .toImage(pixelRatio: 2);
        final bytes = await img.toByteData(format: ui.ImageByteFormat.png);
        File(out).writeAsBytesSync(bytes!.buffer.asUint8List());
      });
    }
    await t.binding.setSurfaceSize(null);
  });
}
