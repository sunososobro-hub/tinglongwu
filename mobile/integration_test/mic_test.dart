// #1728: a host audio clip as the Android emulator's microphone (run by .github/workflows/mobile-emulator.yml).
// The emulator runs with -allow-host-audio and the host's default PulseAudio source is the monitor of a null
// sink (mobile/tool/host_mic.sh). Handshake with `host_mic.sh serve`, through files in <filesDir>/e2e/:
//   test prints TLWU_MIC_PHASE=ready      -> host grants RECORD_AUDIO, loops zh.wav into the sink, writes mic_go
//   test records 3 s (clip), prints TLWU_MIC_PHASE=clip_done -> host stops the clip, writes mic_silent
//   test records 3 s (silent control)
// Asserts clip RMS > [kMicSilenceDb] > silent RMS; prints `TLWU_MIC_RMS clip=<dB> silent=<dB>` for the summary.
import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mobile/logic/pcm_level.dart';
import 'package:mobile/services/model_paths.dart';
import 'package:record/record.dart';

/// A 3 s recording at or below this RMS level (dBFS) counts as silence. Not a product value: the emulator
/// gives digital zeros (-120 dBFS) for a silent host source, and the looped zh clip measured -15..-25 dBFS
/// through the same null-sink monitor on a host (`host_mic.sh rms`), so -60 leaves 35+ dB on each side.
const kMicSilenceDb = -60.0;

/// Records [seconds] s of 16 kHz mono PCM16 from the default microphone, after dropping the first 0.5 s
/// (recorder start-up), and returns the PCM bytes.
Future<Uint8List> recordPcm(AudioRecorder rec, {int seconds = 3}) async {
  const skipBytes = 16000; // 0.5 s of 16 kHz PCM16
  final want = skipBytes + seconds * 32000;
  final buf = BytesBuilder(copy: false);
  final enough = Completer<void>();
  final stream = await rec.startStream(const RecordConfig(
    encoder: AudioEncoder.pcm16bits,
    sampleRate: 16000,
    numChannels: 1,
  ));
  final sub = stream.listen((d) {
    buf.add(d);
    if (buf.length >= want && !enough.isCompleted) enough.complete();
  });
  await enough.future.timeout(Duration(seconds: seconds + 20), onTimeout: () {});
  await rec.stop();
  await sub.cancel();
  final all = buf.takeBytes();
  expect(all.length, greaterThanOrEqualTo(want), reason: 'mic gave ${all.length} bytes, want $want');
  return Uint8List.sublistView(all, skipBytes, want);
}

Future<void> waitForFile(File f, String what) async {
  final sw = Stopwatch()..start();
  while (!f.existsSync()) {
    if (sw.elapsed > const Duration(minutes: 3)) fail('no ${f.path} from host_mic.sh ($what) within 3 min');
    await Future<void>.delayed(const Duration(milliseconds: 250));
  }
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('emulator mic: host clip is heard, silent control is not', (tester) async {
    final e2e = '${(await ModelPaths.resolve()).filesDir}/e2e';
    final go = File('$e2e/mic_go');
    final silent = File('$e2e/mic_silent');
    for (final f in [go, silent]) {
      if (f.existsSync()) f.deleteSync();
    }
    final rec = AudioRecorder();

    // ignore: avoid_print
    print('TLWU_MIC_PHASE=ready');
    await waitForFile(go, 'clip playing');
    final clipDb = pcm16RmsDb(await recordPcm(rec));
    // ignore: avoid_print
    print('TLWU_MIC_PHASE=clip_done clip=${clipDb.toStringAsFixed(1)}');

    await waitForFile(silent, 'clip stopped');
    final silentDb = pcm16RmsDb(await recordPcm(rec));
    await rec.dispose();
    // ignore: avoid_print
    print('TLWU_MIC_RMS clip=${clipDb.toStringAsFixed(1)} silent=${silentDb.toStringAsFixed(1)} '
        'threshold=$kMicSilenceDb');

    expect(clipDb, greaterThan(kMicSilenceDb), reason: 'host clip not heard by the emulator mic');
    expect(silentDb, lessThanOrEqualTo(kMicSilenceDb), reason: 'silent control is not silent');
  });
}
