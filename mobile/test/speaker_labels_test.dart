import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/device_probe.dart';
import 'package:mobile/logic/feature_prefs.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/ui/subtitle_screen.dart' show speakerAccent;

const _fast = DeviceInfo(cores: 8, totalRamMb: 8000, availRamMb: 2500, appVersion: '1.0');
const _bench = BenchResult(asrRtf: 0.08, llmCharsPerSec: 20, llmTokensPerSec: 12, totalMs: 4000);

void main() {
  group('speaker labels (#1155)', () {
    test('default off, round trip, old file without the key stays off', () {
      expect(const FeaturePrefs().speakerLabels, isFalse);
      expect(FeaturePrefs.parse('sourceDraft=1\n').speakerLabels, isFalse);
      final q = FeaturePrefs.parse(const FeaturePrefs(speakerLabels: true).format());
      expect(q.speakerLabels, isTrue);
      expect(q, const FeaturePrefs(speakerLabels: true));
      expect(q.copyWith(speakerLabels: false).speakerLabels, isFalse);
    });

    test('advice: needs the model, never auto, light phones recommended', () {
      expect(adviseSpeakerLabels(_fast, _bench, modelInstalled: false).badge, AdviceBadge.unknown);
      expect(adviseSpeakerLabels(_fast, null, modelInstalled: true).badge, AdviceBadge.unknown);
      expect(adviseSpeakerLabels(_fast, _bench, modelInstalled: true).badge, AdviceBadge.recommend);
      const low = DeviceInfo(cores: 8, availRamMb: 100);
      expect(adviseSpeakerLabels(low, _bench, modelInstalled: true).badge, AdviceBadge.avoid);
      const slow = BenchResult(asrRtf: 0.5, llmCharsPerSec: 20, llmTokensPerSec: 12, totalMs: 1);
      expect(adviseSpeakerLabels(_fast, slow, modelInstalled: true).badge, AdviceBadge.avoid);
    });

    test('the shipped manifest pins the desktop model, outside the base pack', () {
      final m = ModelManifest.parse(
          File('assets/models_manifest.json').readAsStringSync(),
          packsJson: File('assets/voice_packs.json').readAsStringSync());
      final e = m.speakerModel!;
      expect(e.basename, '3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx');
      expect(e.size, 28281164);
      expect(e.sha256, 'aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2');
      expect(m.files.any((f) => f.path == e.path), isFalse); // not in the base models
      expect(m.missingRequired({}).contains(e.path), isFalse); // never required
      expect(m.speakerModelInstalled({}), isFalse);
      expect(m.speakerModelInstalled({e.path}), isTrue);
      expect(m.candidates(e.basename).single.path, e.path); // a picked file is accepted
      expect(m.validate(e.path, e.size, e.sha256).ok, isTrue);
      expect(m.validate(e.path, e.size, 'b' * 64).verdict, Verdict.hashMismatch);
    });

    test('accent colours cycle and differ between neighbours', () {
      expect(speakerAccent(1), isNot(speakerAccent(2)));
      expect(speakerAccent(7), speakerAccent(1));
    });
  });
}
