// #1832 (part 1b of #1617): the phone's Dart text logic against the shared golden cases in
// shared/golden/{clean,prompt}.jsonl. Cases tagged `known_diff: phone` (with an issue number) are
// ones where the phone is known to differ from desktop; they are counted, not asserted. Every other
// case must match exactly. No file under mobile/lib changes here: fixing a difference is a later part.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/prompt.dart';

List<Map<String, dynamic>> _load(String name) {
  // `flutter test` runs with mobile/ as the working directory.
  final f = File('../shared/golden/$name.jsonl');
  return [
    for (final line in f.readAsLinesSync())
      if (line.trim().isNotEmpty) jsonDecode(line) as Map<String, dynamic>,
  ];
}

bool _known(Map<String, dynamic> c) => c['known_diff'] == 'phone' && c['issue'] is int;

void main() {
  final clean = _load('clean');
  final prompt = _load('prompt');

  test('golden files are loaded', () {
    expect(clean.length, greaterThanOrEqualTo(20));
    expect(prompt.length, greaterThanOrEqualTo(10));
  });

  group('clean (PromptBuilder.clean)', () {
    for (var i = 0; i < clean.length; i++) {
      final c = clean[i];
      if (_known(c)) continue;
      test('#$i ${jsonEncode(c['input'])}', () {
        expect(PromptBuilder.clean(c['input'] as String), c['expected']);
      });
    }
  });

  group('prompt (PromptBuilder.userMessage; the phone has no glossary or context)', () {
    for (var i = 0; i < prompt.length; i++) {
      final c = prompt[i];
      if (_known(c)) continue;
      test('#$i ${c['src']}->${c['tgt']} ${jsonEncode(c['text'])}', () {
        final got = PromptBuilder.userMessage(c['text'] as String, src: c['src'] as String, tgt: c['tgt'] as String);
        expect(got, c['expected']);
      });
    }
  });

  test('known_diff summary', () {
    final nClean = clean.where(_known).length;
    final nPrompt = prompt.where(_known).length;
    // ignore: avoid_print
    print('known_diff: clean=$nClean prompt=$nPrompt');
    expect(nClean + nPrompt, greaterThan(0));
  });
}
