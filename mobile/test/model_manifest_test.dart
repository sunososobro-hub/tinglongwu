import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/services/model_paths.dart';

const _a = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const _b = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

ModelManifest _small() => ModelManifest.parse('''
{
  "version": 1,
  "bundle": {"name": "m.zip", "url": "https://example.invalid/m.zip"},
  "files": [
    {"path": "gigaam3/tokens.txt", "size": 10, "sha256": "$_a", "group": "asr"},
    {"path": "silero_vad.onnx", "size": 20, "sha256": "$_b", "group": "vad"},
    {"path": "small.gguf", "size": 30, "sha256": "$_a", "group": "llm"},
    {"path": "big.gguf", "size": 40, "sha256": "$_b", "group": "llm", "url": "https://example.invalid/big.gguf"}
  ]
}''');

void main() {
  group('ModelManifest.validate', () {
    final m = _small();

    test('accepts exact size and hash (hash case-insensitive)', () {
      expect(m.validate('gigaam3/tokens.txt', 10, _a).verdict, Verdict.ok);
      expect(m.validate('silero_vad.onnx', 20, _b.toUpperCase()).ok, isTrue);
    });

    test('rejects wrong size', () {
      final r = m.validate('silero_vad.onnx', 19, _b);
      expect(r.verdict, Verdict.sizeMismatch);
      expect(r.detail, contains('expected 20'));
    });

    test('rejects wrong hash', () {
      expect(m.validate('silero_vad.onnx', 20, _a).verdict, Verdict.hashMismatch);
    });

    test('rejects paths not in the manifest', () {
      expect(m.validate('evil.so', 10, _a).verdict, Verdict.unknownFile);
      expect(m.validate('tokens.txt', 10, _a).verdict, Verdict.unknownFile); // needs gigaam3/
    });
  });

  group('ModelManifest.resolve', () {
    final m = _small();

    test('matches by basename, ignoring folders inside a zip', () {
      expect(m.resolve('tlwu-mobile-models/gigaam3/tokens.txt')?.path, 'gigaam3/tokens.txt');
      expect(m.resolve('tokens.txt')?.path, 'gigaam3/tokens.txt');
      expect(m.resolve(r'models\silero_vad.onnx')?.path, 'silero_vad.onnx');
    });

    test('undoes browser renames of repeated downloads', () {
      expect(m.resolve('big (1).gguf')?.path, 'big.gguf');
      expect(m.resolve('silero_vad-2.onnx')?.path, 'silero_vad.onnx');
      expect(ModelManifest.normalizeName('Qwen3-4B-Q4_K_M (3).gguf'), 'Qwen3-4B-Q4_K_M.gguf');
    });

    test('unknown files resolve to null', () {
      expect(m.resolve('LICENSE'), isNull);
      expect(m.resolve('photo.jpg'), isNull);
      expect(m.resolve('Big.gguf'), isNull); // case-sensitive, like the loader
    });

    test('basenameTargets maps zip entry names to target paths', () {
      expect(m.basenameTargets['tokens.txt'], 'gigaam3/tokens.txt');
      expect(m.basenameTargets.length, 4);
    });
  });

  group('ModelManifest.missingRequired', () {
    final m = _small();

    test('all asr/vad files plus the bundle llm when nothing is there', () {
      expect(m.missingRequired({}), ['gigaam3/tokens.txt', 'silero_vad.onnx', 'small.gguf']);
    });

    test('any one llm satisfies the llm requirement', () {
      expect(m.missingRequired({'gigaam3/tokens.txt', 'silero_vad.onnx', 'big.gguf'}), isEmpty);
      expect(m.missingRequired({'gigaam3/tokens.txt', 'silero_vad.onnx', 'small.gguf'}), isEmpty);
    });
  });

  group('ModelManifest.parse rejects bad manifests', () {
    String one(String file) =>
        '{"bundle": {"name": "m.zip", "url": "u"}, "files": [$file]}';

    test('bad sha256', () {
      expect(() => ModelManifest.parse(one('{"path": "a", "size": 1, "sha256": "xyz", "group": "asr"}')),
          throwsFormatException);
    });

    test('path traversal', () {
      expect(
          () => ModelManifest.parse(
              one('{"path": "../a", "size": 1, "sha256": "$_a", "group": "asr"}')),
          throwsFormatException);
    });

    test('duplicate basenames', () {
      expect(
          () => ModelManifest.parse(one('{"path": "x/a", "size": 1, "sha256": "$_a", "group": "asr"},'
              '{"path": "y/a", "size": 1, "sha256": "$_a", "group": "asr"}')),
          throwsFormatException);
    });
  });

  test('shipped assets/models_manifest.json matches what the app loads', () {
    final m = ModelManifest.parse(File('assets/models_manifest.json').readAsStringSync());
    final paths = m.files.map((f) => f.path).toSet();
    for (final f in ModelPaths.asrFiles) {
      expect(paths, contains('${ModelPaths.asrDirName}/$f'));
    }
    expect(paths, contains(ModelPaths.vadFileName));
    expect(paths, contains(ModelPaths.llmFile));
    // The bundle must hold everything required (only files with their own url may be left out).
    final inBundle = m.files.where((f) => f.url == null).map((f) => f.path).toSet();
    expect(m.missingRequired(inBundle), isEmpty);
    expect(m.bundleUrl, contains('/releases/download/mobile-latest/'));
  });
}
