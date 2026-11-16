import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/logic/lang_pack.dart';
import 'package:mobile/logic/model_manifest.dart';
import 'package:mobile/logic/speech_out.dart';
import 'package:mobile/services/model_paths.dart';

const _a = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const _b = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

ModelManifest _small() => ModelManifest.parse('''
{
  "version": 1,
  "bundle": {"name": "m.zip", "url": "https://example.invalid/m.zip"},
  "files": [
    {"path": "sensevoice/tokens.txt", "size": 10, "sha256": "$_a", "group": "asr"},
    {"path": "silero_vad.onnx", "size": 20, "sha256": "$_b", "group": "vad"},
    {"path": "small.gguf", "size": 30, "sha256": "$_a", "group": "llm"},
    {"path": "big.gguf", "size": 40, "sha256": "$_b", "group": "llm", "url": "https://example.invalid/big.gguf"}
  ]
}''');

const _c = 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc';

String _voice({String lang = 'en', String extra = ''}) => '''
{"packs": [{"id": "voice-$lang", "lang": "$lang", "name": "tlwu-voice-$lang.zip", "url": "u",
  "source": "https://example.invalid/tts-models/vits-ljs.tar.bz2",
  "files": [
    {"path": "tts-$lang/model.onnx", "alias": "vits-ljs.onnx", "size": 100, "sha256": "$_c"},
    {"path": "tts-$lang/tokens.txt", "size": 11, "sha256": "$_b"},
    {"path": "tts-$lang/lexicon.txt", "size": 12, "sha256": "$_a"}$extra
  ]}]}''';

ModelManifest _withVoice() => ModelManifest.parse('''
{
  "bundle": {"name": "m.zip", "url": "https://example.invalid/m.zip"},
  "files": [
    {"path": "sensevoice/tokens.txt", "size": 10, "sha256": "$_a", "group": "asr"},
    {"path": "silero_vad.onnx", "size": 20, "sha256": "$_b", "group": "vad"},
    {"path": "small.gguf", "size": 30, "sha256": "$_a", "group": "llm"}
  ]
}''', packsJson: _voice());

void main() {
  group('ModelManifest.validate', () {
    final m = _small();

    test('accepts exact size and hash (hash case-insensitive)', () {
      expect(m.validate('sensevoice/tokens.txt', 10, _a).verdict, Verdict.ok);
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
      expect(m.validate('tokens.txt', 10, _a).verdict, Verdict.unknownFile); // needs sensevoice/
    });
  });

  group('ModelManifest.resolve', () {
    final m = _small();

    test('matches by basename, ignoring folders inside a zip', () {
      expect(m.resolve('tlwu-mobile-models/sensevoice/tokens.txt')?.path, 'sensevoice/tokens.txt');
      expect(m.resolve('tokens.txt')?.path, 'sensevoice/tokens.txt');
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
      expect(m.basenameTargets['tokens.txt'], 'sensevoice/tokens.txt');
      expect(m.basenameTargets.length, 4);
    });
  });

  group('ModelManifest.missingRequired', () {
    final m = _small();

    test('all asr/vad files plus the bundle llm when nothing is there', () {
      expect(m.missingRequired({}), ['sensevoice/tokens.txt', 'silero_vad.onnx', 'small.gguf']);
    });

    test('any one llm satisfies the llm requirement', () {
      expect(m.missingRequired({'sensevoice/tokens.txt', 'silero_vad.onnx', 'big.gguf'}), isEmpty);
      expect(m.missingRequired({'sensevoice/tokens.txt', 'silero_vad.onnx', 'small.gguf'}), isEmpty);
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

  group('voice packs (#558)', () {
    final m = _withVoice();

    test('a voice is optional: never reported missing', () {
      expect(m.missingRequired({'sensevoice/tokens.txt', 'silero_vad.onnx', 'small.gguf'}), isEmpty);
      expect(m.installedPacks({'sensevoice/tokens.txt'}), isEmpty);
      expect(m.installedPacks({...TtsVoiceLayout.rel('en')}), {'voice-en'});
      expect(m.installedPacks({'tts-en/model.onnx', 'tts-en/tokens.txt'}), isEmpty); // partial
    });

    test('the pack zip (also renamed or the re-zipped upstream folder) maps to tts-en/', () {
      for (final z in ['tlwu-voice-en.zip', 'tlwu-voice-en (1).zip', 'TLWU-voice-en.zip', 'vits-ljs.zip']) {
        final t = m.zipTargetsFor(z);
        expect(t['tokens.txt'], 'tts-en/tokens.txt', reason: z);
        expect(t['model.onnx'], 'tts-en/model.onnx', reason: z);
        expect(t['vits-ljs.onnx'], 'tts-en/model.onnx', reason: z);
        expect(t['lexicon.txt'], 'tts-en/lexicon.txt', reason: z);
        expect(t.containsKey('silero_vad.onnx'), isFalse, reason: z);
      }
    });

    test('any other zip still maps to the models, so tokens.txt stays the ASR one', () {
      final t = m.zipTargetsFor('tlwu-mobile-models.zip');
      expect(t['tokens.txt'], 'sensevoice/tokens.txt');
      expect(t.containsKey('lexicon.txt'), isFalse);
      expect(m.packForZip('photos.zip'), isNull);
    });

    test('a single tokens.txt is kept where its size and SHA-256 match', () {
      final opts = [for (final e in m.candidates('tokens.txt')) e.path];
      expect(opts, ['sensevoice/tokens.txt', 'tts-en/tokens.txt']);
      expect(m.validateAny(opts, 10, _a).entry!.path, 'sensevoice/tokens.txt');
      expect(m.validateAny(opts, 11, _b).entry!.path, 'tts-en/tokens.txt');
      final bad = m.validateAny(opts, 11, _a);
      expect(bad.ok, isFalse);
      expect(bad.entry!.path, 'sensevoice/tokens.txt'); // reported against the first candidate
    });

    test('upstream file names and download renames are accepted', () {
      expect([for (final e in m.candidates('vits-ljs (1).onnx')) e.path], ['tts-en/model.onnx']);
      expect([for (final e in m.candidates('lexicon.txt')) e.path], ['tts-en/lexicon.txt']);
      expect(m.candidates('model.onnx.part'), isEmpty);
    });

    test('voice files are checked like models', () {
      expect(m.validate('tts-en/model.onnx', 100, _c).ok, isTrue);
      expect(m.validate('tts-en/model.onnx', 99, _c).verdict, Verdict.sizeMismatch);
      expect(m.validate('tts-en/model.onnx', 100, _a).verdict, Verdict.hashMismatch);
      expect(m.validate('tts-en/evil.so', 1, _a).verdict, Verdict.unknownFile);
    });

    test('a pack must hold exactly the files the TTS loader reads', () {
      const base = '{"bundle": {"name": "m.zip", "url": "u"}, "files": []}';
      expect(() => ModelManifest.parse(base, packsJson: _voice(lang: 'ru')), returnsNormally);
      expect(
          () => ModelManifest.parse(base,
              packsJson: _voice(extra: ',{"path": "tts-en/x.so", "size": 1, "sha256": "$_a"}')),
          throwsFormatException);
      expect(
          () => ModelManifest.parse(base,
              packsJson: _voice(extra: ',{"path": "tts-en/model.onnx", "size": 1, "sha256": "$_a"}')),
          throwsFormatException);
      final missing = _voice().replaceFirst(RegExp(r',\s*\{"path": "tts-en/lexicon.txt"[^}]*\}'), '');
      expect(() => ModelManifest.parse(base, packsJson: missing), throwsFormatException);
    });
  });

  group('extra-language recognizer pack (#556)', () {
    String asr({String kind = 'asr', String lang = 'ru', String extra = '', bool dropTokens = false}) {
      final tokens = dropTokens ? '' : ',{"path": "asr-$lang/tokens.txt", "size": 13, "sha256": "$_a"}';
      return '{"packs": [{"id": "lang-$lang", "lang": "$lang", "kind": "$kind", '
          '"name": "tlwu-mobile-lang-$lang.zip", "url": "u", '
          '"source": "https://example.invalid/asr-models/sherpa-onnx-zipformer-ru-int8-2025-04-20.tar.bz2", '
          '"files": ['
          '{"path": "asr-$lang/encoder.int8.onnx", "size": 100, "sha256": "$_c"},'
          '{"path": "asr-$lang/decoder.onnx", "size": 11, "sha256": "$_b"},'
          '{"path": "asr-$lang/joiner.int8.onnx", "size": 12, "sha256": "$_a"}'
          '$tokens$extra]}]}';
    }

    const base = '{"bundle": {"name": "m.zip", "url": "u"}, "files": []}';

    test('layout is exactly the transducer files the ASR isolate loads', () {
      final m = ModelManifest.parse(base, packsJson: asr());
      final p = m.packs.single;
      expect(p.isAsr, isTrue);
      expect(p.folder, 'asr-ru');
      expect(p.layout, AsrPackLayout.rel('ru'));
      expect({for (final f in p.files) f.path}, AsrPackLayout.rel('ru').toSet());
      expect(p.files.every((f) => f.group == 'lang'), isTrue);
      expect(() => ModelManifest.parse(base, packsJson: asr(dropTokens: true)), throwsFormatException);
      expect(
          () => ModelManifest.parse(base,
              packsJson: asr(extra: ',{"path": "asr-ru/evil.so", "size": 1, "sha256": "$_a"}')),
          throwsFormatException);
      // A voice's folder is not a recognizer's.
      expect(() => ModelManifest.parse(base, packsJson: asr().replaceAll('asr-ru/', 'tts-ru/')),
          throwsFormatException);
      expect(() => ModelManifest.parse(base, packsJson: asr(kind: 'rocket')), throwsFormatException);
    });

    test('the pack zip maps to asr-ru/, is optional and leaves the models zip alone', () {
      final m = ModelManifest.parse(base, packsJson: asr());
      for (final z in ['tlwu-mobile-lang-ru.zip', 'tlwu-mobile-lang-ru (1).zip', 'TLWU-mobile-lang-ru.zip']) {
        final t = m.zipTargetsFor(z);
        expect(t['encoder.int8.onnx'], 'asr-ru/encoder.int8.onnx', reason: z);
        expect(t['tokens.txt'], 'asr-ru/tokens.txt', reason: z);
        expect(t.length, 4, reason: z);
      }
      // The re-zipped upstream archive (its name ends in a date) is read as the pack too.
      expect(m.packForZip('sherpa-onnx-zipformer-ru-int8-2025-04-20.zip'), same(m.packs.single));
      expect(m.packForZip('tlwu-mobile-models.zip'), isNull);
      expect(m.missingRequired({}), isEmpty);
      expect(m.installedPacks({...AsrPackLayout.rel('ru')}), {'lang-ru'});
      expect(m.installedPacks({'asr-ru/encoder.int8.onnx'}), isEmpty);
    });

    test('AsrPackPaths needs every file', () {
      final v = AsrPackPaths.find('ru', resolve: (r) => '/m/$r', exists: (_) => true)!;
      expect(v.encoder, '/m/asr-ru/encoder.int8.onnx');
      expect(v.decoder, '/m/asr-ru/decoder.onnx');
      expect(v.joiner, '/m/asr-ru/joiner.int8.onnx');
      expect(v.tokens, '/m/asr-ru/tokens.txt');
      expect(
          AsrPackPaths.find('ru', resolve: (r) => '/m/$r', exists: (p) => !p.endsWith('joiner.int8.onnx')),
          isNull);
    });
  });

  test('shipped assets/voice_packs.json has the Russian recognizer pack', () {
    final m = ModelManifest.parse(File('assets/models_manifest.json').readAsStringSync(),
        packsJson: File('assets/voice_packs.json').readAsStringSync());
    final ru = m.packs.singleWhere((p) => p.id == 'lang-ru');
    expect(ru.isAsr, isTrue);
    expect(ru.name, 'tlwu-mobile-lang-ru.zip');
    expect(ru.url, contains('/releases/download/mobile-latest/'));
    expect(m.packForZip(ru.name), same(ru));
    expect(ru.size, lessThan(100 << 20)); // phone friendly
    for (final lang in Conversation.needsExtraAsr) {
      expect(m.packs.any((p) => p.isAsr && p.lang == lang), isTrue, reason: lang);
    }
  });

  test('shipped assets/voice_packs.json loads with the models manifest', () {
    final m = ModelManifest.parse(File('assets/models_manifest.json').readAsStringSync(),
        packsJson: File('assets/voice_packs.json').readAsStringSync());
    final en = m.packs.singleWhere((p) => p.lang == 'en');
    expect({for (final f in en.files) f.path}, TtsVoiceLayout.rel('en').toSet());
    expect(en.url, contains('/releases/download/mobile-latest/'));
    expect(m.packForZip(en.name), same(en));
    // The models zip is unchanged by the voice: tokens.txt there is still the ASR one.
    expect(m.zipTargetsFor(m.bundleName)['tokens.txt'], 'sensevoice/tokens.txt');
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
