import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

// #1169: a Play update that only changes app code must not change the model asset packs, or Play re-sends
// the whole ~1.3 GB. These guard the Gradle settings that keep the pack bytes stable and patchable.
void main() {
  final app = File('android/app/build.gradle.kts').readAsStringSync();

  test('model files are stored uncompressed in the AAB (noCompress covers every bundled extension)', () {
    final m = jsonDecode(File('assets/models_manifest.json').readAsStringSync()) as Map<String, dynamic>;
    final exts = <String>{};
    for (final f in (m['files'] as List).cast<Map<String, dynamic>>()) {
      if (f['url'] != null) continue; // not in the pack
      exts.add((f['path'] as String).split('.').last);
    }
    final block = RegExp(r'noCompress\s*\+=\s*listOf\(([^)]*)\)').firstMatch(app);
    expect(block, isNotNull, reason: 'androidResources { noCompress += listOf(...) } is missing');
    final listed = RegExp(r'"([^"]+)"').allMatches(block!.group(1)!).map((e) => e.group(1)).toSet();
    expect(exts, isNotEmpty);
    expect(listed.containsAll(exts), isTrue, reason: 'noCompress lacks ${exts.difference(listed)}');
  });

  test('model packs stay fast-follow with fixed names', () {
    expect(app, contains('assetPacks += listOf(":models_asr", ":models_llm")'));
    for (final p in ['models_asr', 'models_llm']) {
      final g = File('android/$p/build.gradle.kts').readAsStringSync();
      expect(g, contains('packName.set("$p")'));
      expect(g, contains('deliveryType.set("fast-follow")'));
    }
  });
}
