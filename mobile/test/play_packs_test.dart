import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/services/model_importer.dart';
import 'package:mobile/services/play_packs.dart';

void main() {
  test('docsFromPacks finds manifest files in pack folders, skips ones already imported', () {
    final tmp = Directory.systemTemp.createTempSync('tlwu_play');
    addTearDown(() => tmp.deleteSync(recursive: true));
    final m = ModelManifest.parse('''
{"version": 1, "bundle": {"name": "b.zip", "url": "https://x/b.zip", "note": ""},
 "files": [
  {"path": "sensevoice/tokens.txt", "size": 3, "sha256": "${'0' * 64}", "group": "asr"},
  {"path": "silero_vad.onnx", "size": 2, "sha256": "${'0' * 64}", "group": "vad"},
  {"path": "llm.gguf", "size": 4, "sha256": "${'0' * 64}", "group": "llm"}
 ]}''');
    final models = '${tmp.path}/models';
    final asr = Directory('${tmp.path}/asr')..createSync();
    final llm = Directory('${tmp.path}/llm')..createSync();
    File('${asr.path}/sensevoice/tokens.txt')
      ..createSync(recursive: true)
      ..writeAsStringSync('abc');
    File('${asr.path}/silero_vad.onnx').writeAsStringSync('vv');
    File('${llm.path}/llm.gguf').writeAsStringSync('gggg');
    // already imported with the right size -> not offered again
    File('$models/silero_vad.onnx')
      ..createSync(recursive: true)
      ..writeAsStringSync('vv');

    final docs = PlayPacks.docsFromPacks(ModelImporter(m, models), [asr.path, llm.path]);
    expect(docs.map((d) => d.name), ['tokens.txt', 'llm.gguf']);
    expect(docs.first.uri, Uri.file('${asr.path}/sensevoice/tokens.txt').toString());
    expect(docs.map((d) => d.size), [3, 4]);
  });
}
