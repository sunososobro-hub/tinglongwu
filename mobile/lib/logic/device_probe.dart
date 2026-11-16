import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

/// Pure logic for the device probe and the optional-feature recommendations (#1065): data
/// classes with JSON, the synthetic benchmark audio, the cache file and the thresholds.
/// No Flutter / sherpa imports, so it is unit-testable.

/// What the phone is. Unknown values are null (e.g. iOS gives no RAM / SoC).
class DeviceInfo {
  const DeviceInfo({
    required this.cores,
    this.bigCores,
    this.abi,
    this.totalRamMb,
    this.availRamMb,
    this.sdk,
    this.soc,
    this.model,
    this.appVersion = 'unknown',
  });

  final int cores;

  /// Cores whose max frequency is within 70 % of the fastest core; null = not readable.
  final int? bigCores;
  final String? abi;
  final int? totalRamMb;

  /// RAM still free when the probe ran (after the models were loaded).
  final int? availRamMb;
  final int? sdk;
  final String? soc;
  final String? model;
  final String appVersion;

  Map<String, Object?> toJson() => {
        'cores': cores,
        'bigCores': bigCores,
        'abi': abi,
        'totalRamMb': totalRamMb,
        'availRamMb': availRamMb,
        'sdk': sdk,
        'soc': soc,
        'model': model,
        'appVersion': appVersion,
      };

  static DeviceInfo fromJson(Map<String, Object?> j) {
    int? i(String k) => (j[k] as num?)?.toInt();
    String? s(String k) {
      final v = j[k];
      return v is String && v.isNotEmpty ? v : null;
    }

    return DeviceInfo(
      cores: i('cores') ?? 1,
      bigCores: i('bigCores'),
      abi: s('abi'),
      totalRamMb: i('totalRamMb'),
      availRamMb: i('availRamMb'),
      sdk: i('sdk'),
      soc: s('soc'),
      model: s('model'),
      appVersion: s('appVersion') ?? 'unknown',
    );
  }

  /// One short line for the settings page.
  String summary() {
    final parts = <String>[
      '$cores 核心${bigCores != null && bigCores! > 0 ? '（$bigCores 大核）' : ''}',
      if (totalRamMb != null) '記憶體 ${(totalRamMb! / 1024).toStringAsFixed(1)} GB',
      if (sdk != null) 'Android API $sdk',
      ?soc,
    ];
    return parts.join('・');
  }
}

/// Result of the one-off speed test.
class BenchResult {
  const BenchResult({
    required this.asrRtf,
    required this.llmCharsPerSec,
    required this.llmTokensPerSec,
    required this.totalMs,
  });

  /// SenseVoice decode time / audio time (lower = faster; 0.1 = 10x real time), 4 threads.
  final double asrRtf;

  /// Chinese characters of a fixed sentence per second of translate() wall time (incl. prefill).
  final double llmCharsPerSec;

  /// Decode tokens/s as llama.cpp reports it for that sentence.
  final double llmTokensPerSec;

  /// Wall time of the whole benchmark.
  final int totalMs;

  /// How many times faster than real time the ASR runs.
  double get asrSpeedup => asrRtf > 0 ? 1 / asrRtf : 0;

  Map<String, Object?> toJson() => {
        'asrRtf': asrRtf,
        'llmCharsPerSec': llmCharsPerSec,
        'llmTokensPerSec': llmTokensPerSec,
        'totalMs': totalMs,
      };

  static BenchResult? fromJson(Object? o) {
    if (o is! Map) return null;
    final a = (o['asrRtf'] as num?)?.toDouble();
    final c = (o['llmCharsPerSec'] as num?)?.toDouble();
    final t = (o['llmTokensPerSec'] as num?)?.toDouble();
    if (a == null || c == null || t == null || a <= 0 || c <= 0) return null;
    return BenchResult(
        asrRtf: a,
        llmCharsPerSec: c,
        llmTokensPerSec: t,
        totalMs: (o['totalMs'] as num?)?.toInt() ?? 0);
  }
}

class ProbeReport {
  const ProbeReport(this.info, this.bench);
  final DeviceInfo info;
  final BenchResult? bench;
}

/// 4 s of speech-like audio (voiced harmonics with a slow syllable envelope, 16 kHz mono),
/// deterministic. SenseVoice cost depends on the audio length, not on what is said, so the real
/// time factor measured on it matches real speech closely enough for a recommendation.
Float32List benchAudio({int seconds = 4, int sampleRate = 16000}) {
  final n = seconds * sampleRate;
  final out = Float32List(n);
  var seed = 12345;
  for (var i = 0; i < n; i++) {
    final t = i / sampleRate;
    final f0 = 120 + 25 * math.sin(2 * math.pi * 0.7 * t);
    final env = 0.5 + 0.5 * math.sin(2 * math.pi * 4 * t);
    var v = 0.0;
    for (var h = 1; h <= 12; h++) {
      v += math.sin(2 * math.pi * f0 * h * t) / h;
    }
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    final noise = (seed / 0x7fffffff - 0.5) * 0.02;
    out[i] = (0.25 * env * v / 2 + noise).clamp(-1.0, 1.0).toDouble();
  }
  return out;
}

/// Probe result cache: valid only for the same app version (a new version may change models or
/// the pipeline) and only when the benchmark finished.
class ProbeCache {
  static String encode(DeviceInfo info, BenchResult bench, {DateTime? now}) => jsonEncode({
        'appVersion': info.appVersion,
        'takenAt': (now ?? DateTime.now()).toIso8601String(),
        'info': info.toJson(),
        'bench': bench.toJson(),
      });

  /// Null when missing, damaged, from another app version or without a benchmark.
  static ProbeReport? decode(String? text, {required String appVersion}) {
    if (text == null || text.trim().isEmpty) return null;
    try {
      final j = jsonDecode(text);
      if (j is! Map || j['appVersion'] != appVersion) return null;
      final info = j['info'];
      final bench = BenchResult.fromJson(j['bench']);
      if (info is! Map || bench == null) return null;
      return ProbeReport(DeviceInfo.fromJson(info.cast<String, Object?>()), bench);
    } catch (_) {
      return null;
    }
  }
}

class ProbeStore {
  ProbeStore(this.path);
  final String path;

  ProbeReport? load(String appVersion) {
    try {
      return ProbeCache.decode(File(path).readAsStringSync(), appVersion: appVersion);
    } catch (_) {
      return null;
    }
  }

  void save(DeviceInfo info, BenchResult bench) {
    try {
      File(path).writeAsStringSync(ProbeCache.encode(info, bench));
    } catch (_) {}
  }
}

enum AdviceBadge {
  recommend('建議開啟'),
  tryIt('可試試'),
  avoid('不建議'),
  unknown('測速中');

  const AdviceBadge(this.label);
  final String label;
}

class Advice {
  const Advice(this.badge, this.reason);
  final AdviceBadge badge;
  final String reason;
}

/// Recommendation thresholds. Chosen from the design of the features, to be re-tuned with
/// on-phone benchmark logs (#1065).
class Thresholds {
  /// 原文草稿: a draft decode (SenseVoice, 1 thread, about 2.5x slower than the 4-thread
  /// benchmark) on 4 s of audio has to fit in 2 draft intervals (1.6 s):
  /// 4 s * 2.5 * rtf <= 1.6 s  =>  rtf <= 0.16. Up to 0.30 it still works but lags.
  static const asrRtfGood = 0.16;
  static const asrRtfOk = 0.30;

  /// 中文草稿: a draft translation is about 15 Chinese characters and runs at most every 2 s;
  /// it has to finish in about 1 s (15 chars/s) to leave the LLM free for the final line.
  /// Down to 8 chars/s (about 2 s) it works but may delay the final line.
  static const llmCharsGood = 15.0;
  static const llmCharsOk = 8.0;

  /// The draft recognizer is a second copy of the ASR model (~250 MB): needs this much free RAM
  /// left after the models are loaded (good), or at least the lower bound (ok).
  static const freeRamGoodMb = 700;
  static const freeRamOkMb = 350;

  /// 說話者標籤: free RAM needed for the speaker model (about 60 MB) with a safe margin.
  static const speakerFreeRamMb = 250;

  /// Fewer cores than this: the draft competes with the final decode (ok at best).
  static const coresGood = 6;
}

AdviceBadge _worse(AdviceBadge a, AdviceBadge b) => a.index >= b.index ? a : b;

String _x(double v) => v >= 10 ? v.toStringAsFixed(0) : v.toStringAsFixed(1);

/// 顯示原文草稿: speed of the ASR, free RAM (second recognizer) and core count.
Advice adviseSourceDraft(DeviceInfo info, BenchResult? bench) {
  if (bench == null) return const Advice(AdviceBadge.unknown, '正在測試你的手機速度，稍後會顯示建議。');
  final speed = '約 ${_x(bench.asrSpeedup)} 倍即時';
  var b = bench.asrRtf <= Thresholds.asrRtfGood
      ? AdviceBadge.recommend
      : bench.asrRtf <= Thresholds.asrRtfOk
          ? AdviceBadge.tryIt
          : AdviceBadge.avoid;
  String? ramNote;
  final free = info.availRamMb;
  if (free != null) {
    if (free < Thresholds.freeRamOkMb) {
      b = _worse(b, AdviceBadge.avoid);
      ramNote = '剩餘記憶體只有 $free MB，多載入一份語音模型可能讓 App 被系統關閉';
    } else if (free < Thresholds.freeRamGoodMb) {
      b = _worse(b, AdviceBadge.tryIt);
      ramNote = '剩餘記憶體約 $free MB，偏少';
    }
  }
  String? coreNote;
  if (info.cores < Thresholds.coresGood) {
    b = _worse(b, AdviceBadge.tryIt);
    coreNote = '處理器只有 ${info.cores} 核心';
  }
  final head = switch (b) {
    AdviceBadge.recommend => '你的手機語音辨識很快（$speed），開啟後字幕會更早出現。',
    AdviceBadge.tryIt => '你的手機語音辨識速度普通（$speed），開啟後草稿可能偶爾慢半拍，也較耗電。',
    _ => '你的手機語音辨識偏慢（$speed），開啟後正式字幕可能變慢。',
  };
  final notes = [?ramNote, ?coreNote];
  return Advice(b, notes.isEmpty ? head : '$head ${notes.join('；')}。');
}

/// 中文草稿: speed of the translator. Needs 顯示原文草稿 on (it translates that text).
Advice adviseZhDraft(DeviceInfo info, BenchResult? bench, {bool sourceDraftOn = true}) {
  if (bench == null) return const Advice(AdviceBadge.unknown, '正在測試你的手機速度，稍後會顯示建議。');
  final cps = bench.llmCharsPerSec;
  final speed = '約 ${_x(cps)} 字/秒';
  final b = cps >= Thresholds.llmCharsGood
      ? AdviceBadge.recommend
      : cps >= Thresholds.llmCharsOk
          ? AdviceBadge.tryIt
          : AdviceBadge.avoid;
  final head = switch (b) {
    AdviceBadge.recommend => '你的手機翻譯速度$speed，足夠邊說邊預覽中文。',
    AdviceBadge.tryIt => '你的手機翻譯速度$speed，開啟後正式字幕可能偶爾延遲。',
    _ => '你的手機翻譯速度$speed，開啟後字幕可能延遲。',
  };
  return Advice(b, sourceDraftOn ? head : '$head 需要先開啟「顯示原文草稿」。');
}

/// 說話者標籤 (#1155): a 28 MB embedding model (about 60 MB of RAM in use) on one thread, once
/// per finished sentence and off the subtitle path. Light: it only needs a little free RAM and a
/// phone that is not already struggling with the ASR.
Advice adviseSpeakerLabels(DeviceInfo info, BenchResult? bench, {required bool modelInstalled}) {
  if (!modelInstalled) {
    return const Advice(AdviceBadge.unknown, '需要先匯入說話者模型（約 28 MB）：到「匯入模型」選用項目下載，再回來開啟。');
  }
  if (bench == null) return const Advice(AdviceBadge.unknown, '正在測試你的手機速度，稍後會顯示建議。');
  var b = bench.asrRtf <= Thresholds.asrRtfGood
      ? AdviceBadge.recommend
      : bench.asrRtf <= Thresholds.asrRtfOk
          ? AdviceBadge.tryIt
          : AdviceBadge.avoid;
  String? note;
  final free = info.availRamMb;
  if (free != null && free < Thresholds.speakerFreeRamMb) {
    b = _worse(b, AdviceBadge.avoid);
    note = '剩餘記憶體只有 $free MB';
  }
  if (info.cores < Thresholds.coresGood) b = _worse(b, AdviceBadge.tryIt);
  final head = switch (b) {
    AdviceBadge.recommend => '你的手機夠快，開啟後每句話會標上說話者，幾乎不影響字幕。',
    AdviceBadge.tryIt => '你的手機速度普通，開啟後會多用一點耗電，字幕速度不受影響。',
    _ => '你的手機偏慢或記憶體少，開啟後可能讓字幕變慢。',
  };
  return Advice(b, note == null ? head : '$head $note。');
}
