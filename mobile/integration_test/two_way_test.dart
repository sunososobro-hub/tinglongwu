// #1512: two-way conversation mode end to end on an Android emulator (run by
// .github/workflows/mobile-emulator.yml, which puts the files below into <filesDir>/e2e/):
//   tlwu-mobile-models.zip  base models (mobile-latest), imported like the Import screen does
//   tlwu-voice-en.zip       English read-aloud voice pack (mobile-latest), imported the same way
//   zh.wav                  16 kHz mono PCM16 Mandarin clip, fed as microphone audio in real time
// Asserts the English TTS produced PCM samples and the log has a speakLat line; prints
// `TLWU_SPEAKLAT=<ms>` for the job summary (report only, rules section 20: no timing limit).
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/services/app_log.dart';
import 'package:mobile/services/model_importer.dart';
import 'package:mobile/services/model_paths.dart';
import 'package:mobile/services/pipeline.dart';

/// PCM16 bytes of a 16 kHz mono 16-bit RIFF/WAVE file (fails on any other format).
Uint8List pcm16Of(Uint8List wav) {
  final b = ByteData.sublistView(wav);
  String tag(int o) => String.fromCharCodes(wav.sublist(o, o + 4));
  expect(tag(0), 'RIFF');
  expect(tag(8), 'WAVE');
  var o = 12;
  while (o + 8 <= wav.length) {
    final id = tag(o);
    final len = b.getUint32(o + 4, Endian.little);
    if (id == 'fmt ') {
      expect(b.getUint16(o + 8, Endian.little), 1, reason: 'PCM');
      expect(b.getUint16(o + 10, Endian.little), 1, reason: 'mono');
      expect(b.getUint32(o + 12, Endian.little), 16000, reason: '16 kHz');
      expect(b.getUint16(o + 22, Endian.little), 16, reason: '16-bit');
    } else if (id == 'data') {
      return Uint8List.sublistView(wav, o + 8, (o + 8 + len).clamp(0, wav.length));
    }
    o += 8 + len + (len & 1);
  }
  fail('no data chunk');
}

Future<void> importZip(ModelImporter imp, File zip) async {
  expect(zip.existsSync(), isTrue, reason: '${zip.path} missing (pushed by mobile-emulator.yml)');
  final name = zip.path.split('/').last;
  final r = await imp.run([PickedDoc(Uri.file(zip.path).toString(), name, zip.lengthSync())],
      onProgress: (_, _, _) {});
  // ignore: avoid_print
  print('import $name: ${r.imported.length} file(s), problems=${r.problems}');
  expect(r.cancelled, isFalse);
  expect(r.problems, isEmpty);
  expect(r.imported, isNotEmpty);
}

Future<bool> waitFor(bool Function() done, Duration limit) async {
  final sw = Stopwatch()..start();
  while (!done() && sw.elapsed < limit) {
    await Future<void>.delayed(const Duration(milliseconds: 250));
  }
  return done();
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('two-way: voice pack import, zh clip -> en TTS, speakLat', (tester) async {
    final paths = await ModelPaths.resolve();
    final e2e = '${paths.filesDir}/e2e';
    final imp = ModelImporter(await ModelImporter.loadManifest(), paths.modelsDir);
    await importZip(imp, File('$e2e/tlwu-mobile-models.zip'));
    await importZip(imp, File('$e2e/tlwu-voice-en.zip'));
    expect((await ModelPaths.resolve()).ttsVoice('en'), isNotNull, reason: 'voice pack imported');

    // 對話翻譯 on: I speak zh, the other person en (Pipeline.start reads this file).
    ConversationStore('${paths.filesDir}/conversation.txt').save(
        const Conversation(enabled: true, side: Speaker.me, foreign: 'en', mineLang: 'zh'));
    final pcm = pcm16Of(File('$e2e/zh.wav').readAsBytesSync());

    final p = Pipeline();
    await p.start();
    expect(p.stage, Stage.listening, reason: p.errorText ?? '${p.missingFiles}');
    expect(p.conversation.enabled, isTrue);

    // Real-time mic replay: 100 ms chunks, then 2 s of silence so the VAD closes the segment.
    const chunk = 3200; // 100 ms of 16 kHz PCM16
    final silence = Uint8List(chunk);
    for (var i = 0; i < pcm.length; i += chunk) {
      p.debugFeedMic(Uint8List.fromList(pcm.sublist(i, (i + chunk).clamp(0, pcm.length))));
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }
    for (var i = 0; i < 20; i++) {
      p.debugFeedMic(silence);
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }

    final spoke = await waitFor(
        () => p.spokenSamples > 0 && p.lastSpeakLatencyMs != null, const Duration(minutes: 10));
    await AppLog.instance.flush();
    final log = File(AppLog.instance.path).readAsStringSync();
    final lat = RegExp(r'TTS \[en\] speakLat=(-?\d+)ms').allMatches(log).toList();
    // ignore: avoid_print
    print('TLWU_SPEAKLAT=${lat.isEmpty ? 'none' : lat.last.group(1)} '
        'samples=${p.spokenSamples} subtitles=${p.subtitles.length}');
    p.dispose();

    expect(spoke, isTrue, reason: 'no spoken line within 10 min');
    expect(p.spokenSamples, greaterThan(0));
    expect(lat, isNotEmpty, reason: 'no "TTS [en] speakLat=" line in ${AppLog.instance.path}');
  });
}
