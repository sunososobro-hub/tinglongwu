import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/services/bundled_models.dart';
import 'package:mobile/services/model_importer.dart';

void main() {
  final m = ModelManifest.parse('''
{"version": 1, "bundle": {"name": "b.zip", "url": "https://x/b.zip", "note": ""},
 "files": [
  {"path": "sensevoice/tokens.txt", "size": 3, "sha256": "${'0' * 64}", "group": "asr"},
  {"path": "silero_vad.onnx", "size": 2, "sha256": "${'0' * 64}", "group": "vad"},
  {"path": "llm.gguf", "size": 4, "sha256": "${'0' * 64}", "group": "llm"}
 ]}''');

  test('docsToCopy offers bundled files that are missing, as asset URIs with manifest sizes', () {
    final tmp = Directory.systemTemp.createTempSync('tlwu_bundled');
    addTearDown(() => tmp.deleteSync(recursive: true));
    final docs = BundledModels.docsToCopy(ModelImporter(m, '${tmp.path}/models'),
        {'sensevoice/tokens.txt', 'silero_vad.onnx', 'llm.gguf'});
    expect(docs.map((d) => d.uri),
        ['asset:///sensevoice/tokens.txt', 'asset:///silero_vad.onnx', 'asset:///llm.gguf']);
    expect(docs.map((d) => d.name), ['tokens.txt', 'silero_vad.onnx', 'llm.gguf']);
    expect(docs.map((d) => d.size), [3, 2, 4]);
  });

  test('docsToCopy never touches a file already in the models dir, even with another size', () {
    final tmp = Directory.systemTemp.createTempSync('tlwu_bundled');
    addTearDown(() => tmp.deleteSync(recursive: true));
    final models = '${tmp.path}/models';
    // user-imported / adb-pushed files: one matching, one with a different size
    File('$models/silero_vad.onnx')
      ..createSync(recursive: true)
      ..writeAsStringSync('vv');
    File('$models/llm.gguf').writeAsStringSync('older-build');
    final docs = BundledModels.docsToCopy(
        ModelImporter(m, models), {'sensevoice/tokens.txt', 'silero_vad.onnx', 'llm.gguf'});
    expect(docs.map((d) => d.name), ['tokens.txt']);
  });

  test('docsToCopy is empty without packs (side-loaded APK)', () {
    final tmp = Directory.systemTemp.createTempSync('tlwu_bundled');
    addTearDown(() => tmp.deleteSync(recursive: true));
    expect(BundledModels.docsToCopy(ModelImporter(m, '${tmp.path}/models'), {}), isEmpty);
  });
}
