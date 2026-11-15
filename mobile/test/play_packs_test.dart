import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/services/model_importer.dart';
import 'package:mobile/services/model_paths.dart';

// Fast-follow Play packs (#1031): models are read in place from the pack folders, never copied;
// a file in the models dir always wins.
void main() {
  late Directory tmp;
  late String files, asr, llm;
  const sizes = {
    'sensevoice/model.int8.onnx': 5,
    'sensevoice/tokens.txt': 3,
    'silero_vad.onnx': 2,
    ModelPaths.llmFile: 4,
  };

  void put(String path, int n) => File(path)
    ..createSync(recursive: true)
    ..writeAsStringSync('x' * n);

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('tlwu_play');
    files = '${tmp.path}/files';
    asr = '${tmp.path}/packs/models_asr/assets';
    llm = '${tmp.path}/packs/models_llm/assets';
    Directory('$files/models').createSync(recursive: true);
  });
  tearDown(() => tmp.deleteSync(recursive: true));

  void fillPacks() {
    put('$asr/sensevoice/model.int8.onnx', 5);
    put('$asr/sensevoice/tokens.txt', 3);
    put('$asr/silero_vad.onnx', 2);
    put('$llm/${ModelPaths.llmFile}', 4);
  }

  test('all models come straight from the pack folders, nothing is copied', () {
    fillPacks();
    final p = ModelPaths(files, packDirs: [asr, llm], sizes: sizes);
    expect(p.missing(), isEmpty);
    expect(p.asrFile('model.int8.onnx'), '$asr/sensevoice/model.int8.onnx');
    expect(p.vadPath, '$asr/silero_vad.onnx');
    expect(p.llmPath, '$llm/${ModelPaths.llmFile}');
    expect(Directory('$files/models').listSync(), isEmpty);
  });

  test('a file already in the models dir wins, even with another size, and is left alone', () {
    fillPacks();
    put('$files/models/${ModelPaths.llmFile}', 9); // e.g. imported before, other size
    final p = ModelPaths(files, packDirs: [asr, llm], sizes: sizes);
    expect(p.llmPath, '$files/models/${ModelPaths.llmFile}');
    expect(p.vadPath, '$asr/silero_vad.onnx');
    expect(File(p.llmPath).lengthSync(), 9);
  });

  test('a pack file with the wrong size is not used', () {
    fillPacks();
    put('$llm/${ModelPaths.llmFile}', 7);
    final p = ModelPaths(files, packDirs: [asr, llm], sizes: sizes);
    expect(p.llmPath, '$files/models/${ModelPaths.llmFile}');
    expect(p.missing(), ['$files/models/${ModelPaths.llmFile}']);
  });

  test('no packs (side-loaded APK): models dir only, missing until imported', () {
    final p = ModelPaths(files);
    expect(p.missing(), hasLength(4));
    expect(p.vadPath, '$files/models/silero_vad.onnx');
  });

  test('import screen counts pack files as present', () {
    final m = ModelManifest.parse('''
{"version": 1, "bundle": {"name": "b.zip", "url": "https://x/b.zip", "note": ""},
 "files": [
  {"path": "sensevoice/tokens.txt", "size": 3, "sha256": "${'0' * 64}", "group": "asr"},
  {"path": "silero_vad.onnx", "size": 2, "sha256": "${'0' * 64}", "group": "vad"},
  {"path": "llm.gguf", "size": 4, "sha256": "${'0' * 64}", "group": "llm"}
 ]}''');
    put('$asr/sensevoice/tokens.txt', 3);
    put('$asr/silero_vad.onnx', 1); // wrong size
    put('$files/models/llm.gguf', 4);
    final imp = ModelImporter(m, '$files/models');
    expect(imp.presentFiles(), {'llm.gguf'});
    expect(imp.presentFiles(packDirs: [asr, llm]), {'sensevoice/tokens.txt', 'llm.gguf'});
  });
}
