import 'package:llama_cpp_dart/llama_cpp_dart.dart';

import '../logic/prompt.dart';
import '../logic/zh_check.dart';

class Translation {
  const Translation({
    required this.zh,
    required this.check,
    required this.tokens,
    required this.totalMs,
    required this.firstTokenMs,
    required this.genMs,
    required this.retried,
    required this.nCtx,
  });

  final String zh;
  final ZhCheck check;
  final int tokens;

  /// Wall time of the whole translate() call (incl. retry).
  final int totalMs;

  /// Time to first token of the last attempt (prefill + queueing).
  final int firstTokenMs;

  /// Time from first to last token of the last attempt.
  final int genMs;
  final bool retried;

  /// KV positions used by the last attempt (prompt + generated); the session
  /// is cleared per request so this must stay small and constant.
  final int nCtx;

  double get tokensPerSecond => genMs > 0 ? (tokens - 1) * 1000 / genMs : 0;
}

/// Qwen3 translator on top of llama_cpp_dart (worker isolate, CPU).
class Translator {
  Translator._(this._engine, this._session);

  final LlamaEngine _engine;
  final EngineSession _session;
  final List<ContextPair> _history = [];

  static Future<Translator> load(String modelPath, {int threads = 4}) async {
    final engine = await LlamaEngine.spawn(
      modelParams: ModelParams(path: modelPath, gpuLayers: 0, useMmap: true),
      contextParams: ContextParams(
        nCtx: 768,
        nBatch: 512,
        nUbatch: 512,
        nThreads: threads,
        nThreadsBatch: threads,
      ),
    );
    final session = await engine.createSession();
    return Translator._(engine, session);
  }

  Future<Translation> translate(String ru) async {
    final sw = Stopwatch()..start();
    var attempt = await _run(ru, strict: false);
    var retried = false;
    if (!attempt.check.isTraditional) {
      retried = true;
      final second = await _run(ru, strict: true);
      // Keep the retry if it is better (traditional, or at least not worse).
      if (second.check.isTraditional ||
          second.check.simplifiedCount < attempt.check.simplifiedCount ||
          !attempt.check.hasChinese) {
        attempt = second;
      }
    }
    if (attempt.text.isNotEmpty) {
      _history.add(ContextPair(ru, attempt.text));
      if (_history.length > PromptBuilder.contextSentences) {
        _history.removeAt(0);
      }
    }
    return Translation(
      zh: attempt.text,
      check: attempt.check,
      tokens: attempt.tokens,
      totalMs: sw.elapsedMilliseconds,
      firstTokenMs: attempt.firstTokenMs,
      genMs: attempt.genMs,
      retried: retried,
      nCtx: attempt.nCtx,
    );
  }

  Future<_Attempt> _run(String ru, {required bool strict}) async {
    final prompt = PromptBuilder.build(ru, context: _history, strict: strict);
    await _session.clear();
    final sw = Stopwatch()..start();
    final buf = StringBuffer();
    var tokens = 0;
    var firstMs = 0;
    var nCtx = 0;
    await for (final e in _session.generate(
      prompt: prompt,
      addSpecial: false,
      parseSpecial: true,
      sampler: const SamplerParams(greedy: true),
      maxTokens: 100,
    )) {
      if (e is TokenEvent) {
        if (tokens == 0) firstMs = sw.elapsedMilliseconds;
        tokens++;
        buf.write(e.text);
      } else if (e is DoneEvent) {
        nCtx = e.committedPosition;
      }
    }
    final text = PromptBuilder.clean(buf.toString());
    return _Attempt(
      text,
      checkChinese(text),
      tokens,
      firstMs,
      sw.elapsedMilliseconds - firstMs,
      nCtx,
    );
  }

  Future<void> dispose() async {
    await _session.dispose();
    await _engine.dispose();
  }
}

class _Attempt {
  _Attempt(this.text, this.check, this.tokens, this.firstTokenMs, this.genMs,
      this.nCtx);
  final String text;
  final ZhCheck check;
  final int tokens;
  final int firstTokenMs;
  final int genMs;
  final int nCtx;
}
