import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/device_probe.dart';
import 'package:mobile/logic/draft_gate.dart';
import 'package:mobile/logic/feature_prefs.dart';

const fast = DeviceInfo(cores: 8, totalRamMb: 8000, availRamMb: 2500, appVersion: '1.0');

BenchResult bench({double rtf = 0.08, double cps = 20}) =>
    BenchResult(asrRtf: rtf, llmCharsPerSec: cps, llmTokensPerSec: 12, totalMs: 4000);

void main() {
  group('source draft advice (ASR real-time factor)', () {
    test('thresholds', () {
      expect(adviseSourceDraft(fast, bench(rtf: 0.16)).badge, AdviceBadge.recommend);
      expect(adviseSourceDraft(fast, bench(rtf: 0.17)).badge, AdviceBadge.tryIt);
      expect(adviseSourceDraft(fast, bench(rtf: 0.30)).badge, AdviceBadge.tryIt);
      expect(adviseSourceDraft(fast, bench(rtf: 0.31)).badge, AdviceBadge.avoid);
    });

    test('no benchmark yet is unknown, never a recommendation', () {
      expect(adviseSourceDraft(fast, null).badge, AdviceBadge.unknown);
      expect(adviseZhDraft(fast, null).badge, AdviceBadge.unknown);
    });

    test('low free RAM or few cores downgrade', () {
      const lowRam = DeviceInfo(cores: 8, availRamMb: 500);
      const tiny = DeviceInfo(cores: 8, availRamMb: 200);
      const fewCores = DeviceInfo(cores: 4, availRamMb: 2000);
      expect(adviseSourceDraft(lowRam, bench()).badge, AdviceBadge.tryIt);
      expect(adviseSourceDraft(tiny, bench()).badge, AdviceBadge.avoid);
      expect(adviseSourceDraft(fewCores, bench()).badge, AdviceBadge.tryIt);
      // unknown RAM (iOS) does not penalize
      expect(adviseSourceDraft(const DeviceInfo(cores: 8), bench()).badge, AdviceBadge.recommend);
    });

    test('reason names the measured speed', () {
      expect(adviseSourceDraft(fast, bench(rtf: 0.1)).reason, contains('10 倍即時'));
    });
  });

  group('中文草稿 advice (LLM chars/s)', () {
    test('thresholds', () {
      expect(adviseZhDraft(fast, bench(cps: 15)).badge, AdviceBadge.recommend);
      expect(adviseZhDraft(fast, bench(cps: 14.9)).badge, AdviceBadge.tryIt);
      expect(adviseZhDraft(fast, bench(cps: 8)).badge, AdviceBadge.tryIt);
      expect(adviseZhDraft(fast, bench(cps: 7.9)).badge, AdviceBadge.avoid);
    });

    test('slow phone reason mentions delay and speed', () {
      final a = adviseZhDraft(fast, bench(cps: 5));
      expect(a.reason, contains('約 5.0 字/秒'));
      expect(a.reason, contains('字幕可能延遲'));
    });

    test('needs the source draft', () {
      expect(adviseZhDraft(fast, bench(), sourceDraftOn: false).reason, contains('顯示原文草稿'));
    });
  });

  group('probe cache', () {
    test('round trip for the same app version', () {
      final text = ProbeCache.encode(fast, bench());
      final r = ProbeCache.decode(text, appVersion: '1.0')!;
      expect(r.info.cores, 8);
      expect(r.info.totalRamMb, 8000);
      expect(r.bench!.asrRtf, 0.08);
      expect(r.bench!.llmCharsPerSec, 20);
    });

    test('other app version, junk or missing bench is a miss', () {
      final text = ProbeCache.encode(fast, bench());
      expect(ProbeCache.decode(text, appVersion: '1.1'), isNull);
      expect(ProbeCache.decode('nope', appVersion: '1.0'), isNull);
      expect(ProbeCache.decode(null, appVersion: '1.0'), isNull);
      expect(ProbeCache.decode('{"appVersion":"1.0","info":{"cores":4}}', appVersion: '1.0'), isNull);
    });
  });

  test('benchAudio is 4 s, deterministic and in range', () {
    final a = benchAudio();
    expect(a.length, 64000);
    expect(a.every((v) => v >= -1 && v <= 1), isTrue);
    expect(benchAudio()[12345], a[12345]);
  });

  test('summary line', () {
    expect(const DeviceInfo(cores: 8, bigCores: 4, totalRamMb: 6144, sdk: 34, soc: 'Qualcomm SM8550').summary(),
        '8 核心（4 大核）・記憶體 6.0 GB・Android API 34・Qualcomm SM8550');
  });

  group('DraftTranslateGate (中文草稿, every 2 s)', () {
    test('interval, change, length and busy', () {
      final g = DraftTranslateGate();
      expect(g.shouldRun(nowMs: 5000, text: 'short', translatorBusy: false), isFalse); // < 6 chars
      expect(g.shouldRun(nowMs: 5000, text: 'hello there', translatorBusy: true), isFalse);
      expect(g.shouldRun(nowMs: 5000, text: 'hello there', translatorBusy: false), isTrue);
      g.started(nowMs: 5000, text: 'hello there');
      expect(g.shouldRun(nowMs: 9000, text: 'hello there my', translatorBusy: false), isFalse); // running
      g.finished();
      expect(g.shouldRun(nowMs: 6000, text: 'hello there my', translatorBusy: false), isFalse); // < 2 s
      expect(g.shouldRun(nowMs: 7000, text: 'hello there', translatorBusy: false), isFalse); // same text
      expect(g.shouldRun(nowMs: 7000, text: 'hello there my', translatorBusy: false), isTrue);
    });
  });

  test('defaults: 原文草稿 on, 中文草稿 off; round trip', () {
    const p = FeaturePrefs();
    expect(p.sourceDraft, isTrue);
    expect(p.zhDraft, isFalse);
    final q = FeaturePrefs.parse(const FeaturePrefs(sourceDraft: false, zhDraft: true).format());
    expect(q.sourceDraft, isFalse);
    expect(q.zhDraft, isTrue);
    expect(FeaturePrefs.parse('sourceDraft=1\n').zhDraft, isFalse);
  });
}
