import 'package:llama_cpp_dart/llama_cpp_dart.dart';

import '../logic/context_budget.dart';
import '../logic/prompt.dart';
import '../logic/s2tw.dart';
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
  });

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
class Translator {
  Translator._(this._engine, this._session);

  final LlamaEngine _engine;
  final EngineSession _session;

  static Future<Translator> load(String modelPath, {int threads = 4}) async {
    final engine = await LlamaEngine.spawn(
      modelParams: ModelParams(path: modelPath, gpuLayers: 0, useMmap: true),
      contextParams: ContextParams(
        nCtx: ContextBudget.nCtx,
        nBatch: 512,
        nUbatch: 512,
        nThreads: threads,
        nThreadsBatch: threads,
      ),
    );
    final session = await engine.createSession();
    return Translator._(engine, session);
  }

  /// [src] is the ASR-detected source language code (see [PromptBuilder]).
  Future<Translation> translate(String ru, {String src = 'ru'}) async {
    final sw = Stopwatch()..start();
    final prompt = PromptBuilder.build(ru.trim(), src: src);
    await _session.clear();
    final buf = StringBuffer();
    var tokens = 0;
    var firstMs = 0;
    var nCtx = 0;
    await for (final e in _session.generate(
      prompt: prompt,
      addSpecial: false,
      parseSpecial: true,
      sampler: const SamplerParams(greedy: true),
      // Clamped so prompt + output stay inside nCtx (#533).
      maxTokens: ContextBudget.maxTokens(ru),
    )) {
      if (e is TokenEvent) {
        if (tokens == 0) firstMs = sw.elapsedMilliseconds;
        tokens++;
        buf.write(e.text);
      } else if (e is DoneEvent) {
        nCtx = e.committedPosition;
      }
    }
    // Same as desktop hymt: the card prompt asks for 中文, so convert to zh-Hant (Taiwan) afterwards.
    final text = toTraditionalTw(PromptBuilder.clean(buf.toString()));
    return Translation(
      zh: text,
      check: checkChinese(text),
      tokens: tokens,
      totalMs: sw.elapsedMilliseconds,
      firstTokenMs: firstMs,
      genMs: sw.elapsedMilliseconds - firstMs,
      nCtx: nCtx,
    );
  }

  Future<void> dispose() async {
    await _session.dispose();
    await _engine.dispose();
  }
}
