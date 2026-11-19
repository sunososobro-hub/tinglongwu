import 'dart:io' show Platform;

import 'package:llama_cpp_dart/llama_cpp_dart.dart';

import '../logic/context_budget.dart';
import '../logic/passthrough.dart';
import '../logic/prompt.dart';
import '../logic/s2tw.dart';
import '../logic/target_script.dart';
import '../logic/utf8_stream.dart';
import '../logic/zh_quotes.dart';
import '../logic/zh_check.dart';

class Translation {
  const Translation({
    required this.zh,
    required this.check,
    required this.tokens,
    required this.totalMs,
    required this.firstTokenMs,
    required this.genMs,
    required this.nCtx,
    this.scriptCheck = '',
  });

  /// #1616 target-script check: '' = fine, 'retry' = fixed by the other template, 'dropped'.
  final String scriptCheck;

  final String zh;
  final ZhCheck check;
  final int tokens;

  /// Wall time of the whole translate() call.
  final int totalMs;

  /// Time to first token (prefill + queueing).
  final int firstTokenMs;

  /// Time from first to last token.
  final int genMs;

  /// KV positions used (prompt + generated); the session
  /// is cleared per request so this must stay small and constant.
  final int nCtx;

  double get tokensPerSecond => genMs > 0 ? (tokens - 1) * 1000 / genMs : 0;
}

/// Hy-MT2-1.8B translator on top of llama_cpp_dart (worker isolate, CPU).
///
/// Same prompt as desktop `tinglongwu/hymt.py` (see [PromptBuilder]); one
/// stateless request per sentence, like desktop (no context memory). Greedy
/// decoding = desktop with TLWU_HYMT_TEMP=0 (repeatable output on the phone).
abstract class LineTranslator {
  Future<Translation> translate(String ru, {String src = 'ru', String tgt = 'zh'});
}

/// The single call `Pipeline._drain` makes per queued line; forwards the
/// ASR-detected language so a dropped `src` is observable in tests (#581).
Future<Translation> translateLine(LineTranslator tr, String text, String lang,
        {String tgt = 'zh'}) =>
    tr.translate(text, src: lang, tgt: tgt);

class Translator implements LineTranslator {
  Translator._(this._engine, this._session);

  final LlamaEngine _engine;
  final EngineSession _session;

  static Future<Translator> load(String modelPath, {int threads = 4}) async {
    final modelParams = ModelParams(path: modelPath, gpuLayers: 0, useMmap: true);
    final contextParams = ContextParams(
      nCtx: ContextBudget.nCtx,
      nBatch: 512,
      nUbatch: 512,
      nThreads: threads,
      nThreadsBatch: threads,
    );
    // iOS: llama.xcframework is linked into the app by the plugin's Swift package, so the
    // symbols are already in the process (no libllama.so to dlopen as on Android).
    final engine = Platform.isIOS
        ? await LlamaEngine.spawnFromProcess(modelParams: modelParams, contextParams: contextParams)
        : await LlamaEngine.spawn(modelParams: modelParams, contextParams: contextParams);
    final session = await engine.createSession();
    return Translator._(engine, session);
  }

  /// [src] is the ASR-detected source language code (see [PromptBuilder]).
  @override
  Future<Translation> translate(String ru,
      {String src = 'ru', String tgt = 'zh', bool Function()? shouldStop}) async {
    final sw = Stopwatch()..start();
    final pass = passthrough(ru, src, tgt: tgt);
    if (pass != null) {
      // src == tgt: no LLM call (#581), same as desktop.
      return Translation(
        zh: pass,
        check: checkChinese(pass),
        tokens: 0,
        totalMs: sw.elapsedMilliseconds,
        firstTokenMs: 0,
        genMs: 0,
        nCtx: 0,
      );
    }
    var tokens = 0;
    var firstMs = 0;
    var nCtx = 0;
    var stopped = false;
    // One generation with the card template ([alt] = the other one, #1616), cleaned.
    Future<String> run(bool alt) async {
      final prompt = PromptBuilder.build(ru.trim(), src: src, tgt: tgt, alt: alt);
      await _session.clear();
      final buf = StringBuffer();
      // Own byte-level decoder: pieces are raw bytes and can split a character (see Utf8Stream).
      final utf8 = Utf8Stream();
      await for (final e in _session.generate(
        prompt: prompt,
        addSpecial: false,
        parseSpecial: true,
        sampler: const SamplerParams(greedy: true),
        // Faithfulness: output budget tied to the input length (a longer answer is invented text),
        // clamped so prompt + output stay inside nCtx (#533).
        maxTokens: ContextBudget.maxTokensFor(ru, src: src, tgt: tgt),
      )) {
        if (e is TokenEvent) {
          if (tokens == 0) firstMs = sw.elapsedMilliseconds;
          tokens++;
          // A draft translation (#1065) yields to the next final line: stop at the next token.
          if (shouldStop != null && shouldStop()) {
            stopped = true;
            break;
          }
          buf.write(utf8.add(e.bytes));
          // One sentence in, one line out: stop at the first line break after some text, so the
          // model cannot continue with a second paragraph of its own. A line that is only the
          // echoed card instruction does not count (#1615): the translation may follow it.
          if (PromptBuilder.lineDone(buf.toString())) break;
        } else if (e is DoneEvent) {
          nCtx = e.committedPosition;
        }
      }
      buf.write(utf8.close());
      return sanitizeOutput(PromptBuilder.clean(buf.toString()));
    }

    // #1616: an answer in the wrong script (English for a Russian target) is retried once with
    // the other card template, then dropped. A stopped draft is never retried.
    final checked = await translateInScript(ru, tgt, (alt) async {
      if (alt && stopped) return '';
      return run(alt);
    });
    final cleaned = checked.text;
    // Same as desktop hymt: the card prompt asks for 中文, so convert to zh-Hant (Taiwan) afterwards.
    final text = tgt == 'zh' ? toTwQuotes(toTraditionalTw(cleaned)) : cleaned;
    return Translation(
      zh: text,
      check: checkChinese(text),
      tokens: tokens,
      totalMs: sw.elapsedMilliseconds,
      firstTokenMs: firstMs,
      genMs: sw.elapsedMilliseconds - firstMs,
      nCtx: nCtx,
      scriptCheck: checked.note,
    );
  }

  Future<void> dispose() async {
    await _session.dispose();
    await _engine.dispose();
  }
}
