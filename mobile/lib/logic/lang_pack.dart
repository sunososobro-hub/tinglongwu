import 'conversation.dart';

/// Optional extra-language speech recognizer (#556 part 2, 對話翻譯 with Russian): SenseVoice
/// only hears zh/en/ja/ko/yue, so another language comes as its own pack, imported like the
/// models (`assets/voice_packs.json`, kind `asr`).
///
/// A sherpa-onnx offline transducer (zipformer), layout relative to the models dir:
///   `asr-LANG/encoder.int8.onnx`, `decoder.onnx`, `joiner.int8.onnx`, `tokens.txt`
class AsrPackLayout {
  static const files = ['encoder.int8.onnx', 'decoder.onnx', 'joiner.int8.onnx', 'tokens.txt'];

  static String dir(String lang) => 'asr-$lang';

  /// Manifest-style relative paths of the recognizer for [lang].
  static List<String> rel(String lang) => [for (final f in files) '${dir(lang)}/$f'];
}

/// Absolute paths of an installed extra recognizer.
class AsrPackPaths {
  const AsrPackPaths({
    required this.lang,
    required this.encoder,
    required this.decoder,
    required this.joiner,
    required this.tokens,
  });
  final String lang, encoder, decoder, joiner, tokens;

  /// Null when any file is missing (no partial recognizer).
  static AsrPackPaths? find(
    String lang, {
    required String Function(String rel) resolve,
    required bool Function(String path) exists,
  }) {
    final p = [for (final r in AsrPackLayout.rel(lang)) resolve(r)];
    if (!p.every(exists)) return null;
    return AsrPackPaths(lang: lang, encoder: p[0], decoder: p[1], joiner: p[2], tokens: p[3]);
  }
}

/// How the ASR isolate decodes in 對話翻譯 when the other person's language needs an extra
/// recognizer ([Conversation.needsExtraAsr]). SenseVoice always listens first (it knows
/// my language); a segment it does not hear as [mine] is decoded again by the extra recognizer.
class AsrRoute {
  const AsrRoute({required this.pack, required this.mine, this.force});
  final AsrPackPaths pack;

  /// My language (SenseVoice's own).
  final String mine;

  /// Manual side button: true = the other person is talking (always the extra recognizer),
  /// false = I am (never). Null = decide from the language SenseVoice detects.
  final bool? force;

  /// Which recognizer takes a segment SenseVoice heard as [detected] (pure).
  bool useExtra(String detected) =>
      force ?? langFamily(detected) != langFamily(mine);
}

/// Pill text in 對話模式 when the other person's language has no recognizer yet; '' otherwise.
String asrStatusLabel({required String foreign, required bool missing}) =>
    missing ? '未安裝${_langName[foreign] ?? foreign}辨識包，聽不懂對方' : '';

const _langName = {'ru': '俄語'};
