import 's2tw.dart';

/// Source-language == target-language passthrough (desktop `HyMT.translate`
/// returns the text unchanged when src == tgt). With the default target zh,
/// Mandarin speech (`zh`) needs no LLM call; show it as zh-Hant (Taiwan).
/// Returns null when the text must go through the translator.
String? passthrough(String text, String src, {String tgt = 'zh'}) {
  if (src != tgt) return null;
  final t = text.trim();
  return tgt == 'zh' ? toTraditionalTw(t) : t;
}

/// Target zh (the default) form of [passthrough].
String? zhPassthrough(String text, String src) => passthrough(text, src);

/// Routes one line: passthrough when src == [tgt], otherwise calls [generate]
/// with the detected [src] (so a dropped language code is observable in tests).
Future<String> routeTranslate(
  String text,
  String src,
  Future<String> Function(String text, String src) generate, {
  String tgt = 'zh',
}) async =>
    passthrough(text, src, tgt: tgt) ?? await generate(text, src);
