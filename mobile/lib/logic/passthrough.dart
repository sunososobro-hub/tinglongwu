import 's2tw.dart';

/// Source-language == target-language passthrough (desktop `HyMT.translate`
/// returns the text unchanged when src == tgt). The target is always zh, so
/// Mandarin speech (`zh`) needs no LLM call; show it as zh-Hant (Taiwan).
/// Returns null when the text must go through the translator.
String? zhPassthrough(String text, String src) =>
    src == 'zh' ? toTraditionalTw(text.trim()) : null;

/// Routes one line: passthrough for `zh`, otherwise calls [generate] with the
/// detected [src] (so a dropped language code is observable in tests).
Future<String> routeTranslate(
  String text,
  String src,
  Future<String> Function(String text, String src) generate,
) async =>
    zhPassthrough(text, src) ?? await generate(text, src);
