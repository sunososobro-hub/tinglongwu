// #1832 (part 1b of #1617): the phone's Dart text logic against the shared golden cases in
// shared/golden/{clean,prompt}.jsonl. Cases tagged `known_diff: phone` (with an issue number) are
// ones where the phone is known to differ from desktop; they are counted, not asserted. Every other
// case must match exactly. #1896: the prompt cases carry no known_diff any more (glossary, context, yue
// and hi come from shared/mt_spec.json).
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/prompt.dart';
import 'package:mobile/logic/target_script.dart';

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
  final script = _load('script');

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

  group('prompt (PromptBuilder.userMessage)', () {
    for (var i = 0; i < prompt.length; i++) {
      final c = prompt[i];
      if (_known(c)) continue;
      test('#$i ${c['src']}->${c['tgt']} ${jsonEncode(c['text'])}', () {
        final terms = c['terms'] == null
            ? null
            : [for (final t in c['terms'] as List) ((t as List)[0] as String, t[1] as String)];
        final got = PromptBuilder.userMessage(c['text'] as String,
            src: c['src'] as String, tgt: c['tgt'] as String, terms: terms, context: c['context'] as String?);
        expect(got, c['expected']);
      });
    }
  });

  group('script (wrongScript, #1819)', () {
    test('has at least 6 wrong-script cases and no known_diff', () {
      expect(script.where((c) => c['wrong'] == true).length, greaterThanOrEqualTo(6));
      expect(script.where((c) => c.containsKey('known_diff')), isEmpty);
    });
    for (var i = 0; i < script.length; i++) {
      final c = script[i];
      test('#$i ${c['tgt']} ${jsonEncode(c['out'])}', () {
        expect(wrongScript(c['src'] as String, c['out'] as String, c['tgt'] as String), c['wrong']);
      });
    }
  });

  test('known_diff summary', () {
    final nClean = clean.where(_known).length;
    final nPrompt = prompt.where(_known).length;
    // ignore: avoid_print
    print('known_diff: clean=$nClean prompt=$nPrompt');
    expect(nPrompt, 0); // #1896: every prompt case matches on both runners
    expect(nClean, greaterThanOrEqualTo(0));
  });
}
