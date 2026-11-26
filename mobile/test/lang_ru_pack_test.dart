import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/logic/optional_groups.dart';
import 'package:mobile/ui/import_screen.dart';

const _a = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

ModelManifest _manifest() => ModelManifest.parse('''
{
  "bundle": {"name": "base-bundle.zip", "url": "https://example.invalid/base-bundle.zip"},
  "files": [
    {"path": "sensevoice/tokens.txt", "size": 10, "sha256": "$_a", "group": "asr"},
    {"path": "small.gguf", "size": 30, "sha256": "$_a", "group": "llm"}
  ],
  "optional_groups": {
    "lang-ru": {
      "zip": "tlwu-mobile-lang-ru.zip",
      "note": "GigaAM v3",
      "files": [
        {"path": "gigaam3/encoder.int8.onnx", "size": 11, "sha256": "$_a", "group": "lang-ru"},
        {"path": "gigaam3/decoder.onnx", "size": 12, "sha256": "$_a", "group": "lang-ru"},
        {"path": "gigaam3/joiner.onnx", "size": 13, "sha256": "$_a", "group": "lang-ru"},
        {"path": "gigaam3/tokens.txt", "size": 14, "sha256": "$_a", "group": "lang-ru"}
      ]
    }
  }
}''');

void _file(String path, int size) => File(path)
  ..createSync(recursive: true)
  ..writeAsBytesSync(List.filled(size, 0));

void _base(Directory d) {
  _file('${d.path}/sensevoice/tokens.txt', 10);
  _file('${d.path}/small.gguf', 30);
}

void _ru(Directory d) {
  _file('${d.path}/gigaam3/encoder.int8.onnx', 11);
  _file('${d.path}/gigaam3/decoder.onnx', 12);
  _file('${d.path}/gigaam3/joiner.onnx', 13);
  _file('${d.path}/gigaam3/tokens.txt', 14);
}

Set<String> _present(ModelManifest m, Directory d) => {
      for (final f in m.allFiles)
        if (File('${d.path}/${f.path}').existsSync() &&
            File('${d.path}/${f.path}').lengthSync() == f.size)
          f.path
    };

void main() {
  late Directory dir;
  setUp(() => dir = Directory.systemTemp.createTempSync('tlwu_ru'));
  tearDown(() => dir.deleteSync(recursive: true));

  test('pack detection: default only, then default + ru', () {
    final m = _manifest();
    _base(dir);
    expect(detectPacks(m, _present(m, dir)).map((p) => p.id), ['default']);
    _ru(dir);
    expect(detectPacks(m, _present(m, dir)).map((p) => p.id), ['default', 'lang-ru']);
  });

  test('a partial ru folder is not the ru pack', () {
    final m = _manifest();
    _base(dir);
    _file('${dir.path}/gigaam3/tokens.txt', 14);
    expect(detectPacks(m, _present(m, dir)).map((p) => p.id), ['default']);
  });

  test('the ru zip maps to gigaam3/ and the models zip is left alone', () {
    final m = _manifest();
    final t = m.zipTargetsFor('tlwu-mobile-lang-ru (1).zip');
    expect(t['joiner.onnx'], 'gigaam3/joiner.onnx');
    expect(t.length, 4);
    expect(m.zipTargetsFor('base-bundle.zip').containsKey('joiner.onnx'), isFalse);
    expect(m.missingRequired({}), ['sensevoice/tokens.txt', 'small.gguf']);
  });

  test('ASR router picks GigaAM for ru only when the pack is installed', () {
    String resolve(String r) => '/m/$r';
    final only = <String>{'asr-ru/encoder.int8.onnx', 'asr-ru/decoder.onnx', 'asr-ru/joiner.int8.onnx', 'asr-ru/tokens.txt'};
    final giga = {...GigaamLayout.rel.map(resolve)};
    // Nothing installed: no extra recognizer.
    expect(pickExtraAsr('ru', resolve: resolve, exists: (_) => false), isNull);
    // GigaAM installed: ru uses it.
    final g = pickExtraAsr('ru', resolve: resolve, exists: giga.contains)!;
    expect(g.encoder, '/m/gigaam3/encoder.int8.onnx');
    expect(g.joiner, '/m/gigaam3/joiner.onnx');
    expect(g.modelType, 'nemo_transducer');
    // A truncated GigaAM file falls back to the good asr-ru pack (transducer).
    final both = {...giga, ...only.map(resolve)};
    final fb = pickExtraAsr('ru',
        resolve: resolve, exists: both.contains, goodCopy: (rel) => !rel.endsWith('joiner.onnx'))!;
    expect(fb.encoder, '/m/asr-ru/encoder.int8.onnx');
    expect(fb.modelType, 'transducer');
    // GigaAM incomplete: not used (earlier pack still wins if there).
    final partial = giga.where((p) => !p.endsWith('joiner.onnx')).toSet();
    expect(pickExtraAsr('ru', resolve: resolve, exists: partial.contains), isNull);
    final old = pickExtraAsr('ru', resolve: resolve, exists: only.map(resolve).toSet().contains)!;
    expect(old.encoder, '/m/asr-ru/encoder.int8.onnx');
    // Another language never gets GigaAM.
    expect(pickExtraAsr('en', resolve: resolve, exists: giga.contains), isNull);
  });

  testWidgets('import screen lists default and ru when ru is installed', (t) async {
    _base(dir);
    _ru(dir);
    await t.pumpWidget(MaterialApp(home: ImportScreen(modelsDir: dir.path, manifest: _manifest())));
    await t.pumpAndSettle();
    await t.dragUntilVisible(find.byKey(const Key('installedPack:lang-ru')),
        find.byType(ListView), const Offset(0, -300));
    expect(find.byKey(const Key('installedPack:default')), findsOneWidget);
    expect(find.byKey(const Key('installedPack:lang-ru')), findsOneWidget);
  });

  testWidgets('import screen lists only default without the ru files', (t) async {
    _base(dir);
    await t.pumpWidget(MaterialApp(home: ImportScreen(modelsDir: dir.path, manifest: _manifest())));
    await t.pumpAndSettle();
    await t.dragUntilVisible(find.byKey(const Key('installedPack:default')),
        find.byType(ListView), const Offset(0, -300));
    expect(find.byKey(const Key('installedPack:default')), findsOneWidget);
    expect(find.byKey(const Key('installedPack:lang-ru')), findsNothing);
  });

  test('shipped manifest: lang-ru group is exactly the GigaAM layout', () {
    final m = ModelManifest.parse(File('assets/models_manifest.json').readAsStringSync());
    final g = m.optionalGroups.singleWhere((g) => g.id == 'lang-ru');
    expect({for (final f in g.files) f.path}, GigaamLayout.rel.toSet());
    expect(m.zipTargetsFor(g.zip)['encoder.int8.onnx'], 'gigaam3/encoder.int8.onnx');
    expect(m.missingRequired({}).any((p) => p.startsWith('gigaam3/')), isFalse);
  });
}
