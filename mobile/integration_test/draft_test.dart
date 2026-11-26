// #1729 (parent #1064): the grey source-text draft (顯示原文草稿) end to end on an Android emulator (run by
// .github/workflows/mobile-emulator.yml after mic_test.dart, with the same files in <filesDir>/e2e/ as
// two_way_test.dart). The host zh clip is fed as microphone audio in real time, round after round, with the
// toggle switched at run time (Pipeline.setSourceDraft, the same call as the settings switch):
//   warm-up (on, not measured), then off, on, off, on, off, on.
// Asserts for every "on" round: (a) a draft line (Pipeline.draftRu, which SubtitleScreen draws in
// UiColors.draft grey) appears before the round's first final source line, (b) when the final line arrives the
// draft is gone ('' or the '…' speaking marker) and no draft is left after the round; for every "off" round no
// draft text appears at all. (c) Median final ASR+MT latency (Pipeline.lastLatencyMs: estimated speech end ->
// translation done) on vs off is compared with [kDraftLatencyMarginMs] and printed as
// `TLWU_DRAFT_LAT on=<ms> off=<ms> margin=<ms> within=<yes|no>` for the job summary. (c) is report-only: rules
// section 20 allows a timing limit only after >= 10 baseline runs of the current build.
// Uses Pipeline.debugFeedMic (as two_way_test.dart) rather than the emulator microphone: each round needs a
// clean start and end of speech, and the host loop of mic_test.dart repeats the clip without a gap.
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mobile/logic/conversation.dart';
import 'package:mobile/services/model_importer.dart';
import 'package:mobile/services/model_paths.dart';
import 'package:mobile/services/pipeline.dart';

import 'two_way_test.dart' show importZip, pcm16Of, waitFor;

/// Draft-on median latency may exceed draft-off by at most this much (ms). Report-only until 10 baseline runs
/// exist (rules section 20); then set it to the baseline p95 difference plus a stated margin and assert it.
const kDraftLatencyMarginMs = 1500;

/// Measured rounds per toggle state.
const kRoundsPerState = 3;

class DraftRound {
  DraftRound(this.draftOn);
  final bool draftOn;
  int? firstDraftMs; // first draft text, ms from the round start
  int? firstFinalMs; // first final source line of this round
  String? draftAtFinal; // Pipeline.draftRu when that final line arrived
  String draftAfter = ''; // Pipeline.draftRu once the round is over
  String finalText = '';
  int? latencyMs;
  bool done = false;

  @override
  String toString() => 'draft=${draftOn ? 'on' : 'off'} firstDraft=${firstDraftMs ?? '-'} '
      'firstFinal=${firstFinalMs ?? '-'} atFinal="${draftAtFinal ?? '-'}" after="$draftAfter" '
      'lat=${latencyMs ?? '-'} final="$finalText"';
}

/// Median of [v] (mean of the middle two for an even count); null when empty.
double? median(List<int> v) {
  if (v.isEmpty) return null;
  final s = [...v]..sort();
  final m = s.length ~/ 2;
  return s.length.isOdd ? s[m].toDouble() : (s[m - 1] + s[m]) / 2;
}

Future<DraftRound> runRound(Pipeline p, Uint8List pcm, {required bool draftOn}) async {
  p.setSourceDraft(draftOn);
  expect(p.prefs.sourceDraft, draftOn);
  final r = DraftRound(draftOn);
  final old = p.subtitles.toSet();
  Iterable<Subtitle> fresh() => p.subtitles.where((s) => !old.contains(s));
  final sw = Stopwatch()..start();
  void watch() {
    final d = p.draftRu;
    if (d.isNotEmpty && d != '…') r.firstDraftMs ??= sw.elapsedMilliseconds;
    if (r.firstFinalMs == null && fresh().isNotEmpty) {
      r.firstFinalMs = sw.elapsedMilliseconds;
      r.draftAtFinal = d;
    }
  }

  p.addListener(watch);
  // Real-time mic replay: 100 ms chunks, then 2 s of silence so the VAD closes the segment.
  const chunk = 3200; // 100 ms of 16 kHz PCM16
  final silence = Uint8List(chunk);
  for (var i = 0; i < pcm.length; i += chunk) {
    p.debugFeedMic(Uint8List.fromList(pcm.sublist(i, (i + chunk).clamp(0, pcm.length))));
    await Future<void>.delayed(const Duration(milliseconds: 100));
  }
  for (var i = 0; i < 20; i++) {
    p.debugFeedMic(silence);
    await Future<void>.delayed(const Duration(milliseconds: 100));
  }
  r.done = await waitFor(
      () => p.queued == 0 && fresh().isNotEmpty && fresh().every((s) => s.zh != null), const Duration(minutes: 4));
  r.latencyMs = p.lastLatencyMs;
  r.finalText = fresh().map((s) => s.ru).join(' ');
  // Past DraftTracker.holdAfterSpeechEndMs, and time for the spoken reply so it does not load the next round.
  await Future<void>.delayed(const Duration(seconds: 5));
  r.draftAfter = p.draftRu;
  p.removeListener(watch);
  // ignore: avoid_print
  print('TLWU_DRAFT_ROUND $r');
  return r;
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('draft: grey draft before the final, final replaces it, latency on vs off', (tester) async {
    final paths = await ModelPaths.resolve();
    final e2e = '${paths.filesDir}/e2e';
    final imp = ModelImporter(await ModelImporter.loadManifest(), paths.modelsDir);
    await importZip(imp, File('$e2e/tlwu-mobile-models.zip'));
    await importZip(imp, File('$e2e/tlwu-voice-en.zip'));

    // Same route as two_way_test.dart: I speak zh, the other person en (zh is not a language that needs the
    // extra recognizer, so drafts are shown).
    ConversationStore('${paths.filesDir}/conversation.txt').save(
        const Conversation(enabled: true, side: Speaker.me, foreign: 'en', mineLang: 'zh'));
    final pcm = pcm16Of(File('$e2e/zh.wav').readAsBytesSync());

    final p = Pipeline();
    await p.start();
    expect(p.stage, Stage.listening, reason: p.errorText ?? '${p.missingFiles}');

    // Warm-up: the draft recognizer isolate and the LLM start on first use; not measured.
    final warm = await runRound(p, pcm, draftOn: true);
    final rounds = <DraftRound>[];
    for (var i = 0; i < kRoundsPerState; i++) {
      rounds.add(await runRound(p, pcm, draftOn: false));
      rounds.add(await runRound(p, pcm, draftOn: true));
    }
    p.dispose();

    final on = rounds.where((r) => r.draftOn).toList();
    final off = rounds.where((r) => !r.draftOn).toList();
    final onMed = median([for (final r in on) if (r.latencyMs != null) r.latencyMs!]);
    final offMed = median([for (final r in off) if (r.latencyMs != null) r.latencyMs!]);
    final within = onMed != null && offMed != null && onMed <= offMed + kDraftLatencyMarginMs;
    // ignore: avoid_print
    print('TLWU_DRAFT_LAT on=${onMed?.round() ?? 'n/a'} off=${offMed?.round() ?? 'n/a'} '
        'margin=$kDraftLatencyMarginMs within=${within ? 'yes' : 'no'} '
        'on_runs=${on.map((r) => r.latencyMs).join(',')} off_runs=${off.map((r) => r.latencyMs).join(',')} '
        'warmup_lat=${warm.latencyMs}');

    for (final r in [warm, ...rounds]) {
      expect(r.done, isTrue, reason: 'no translated final line within 4 min: $r');
      expect(r.finalText.trim(), isNotEmpty, reason: '$r');
      expect(r.latencyMs, isNotNull, reason: '$r');
    }
    for (final r in on) {
      // (a) a draft appears before the final source text.
      expect(r.firstDraftMs, isNotNull, reason: 'no draft text with 顯示原文草稿 on: $r');
      expect(r.firstDraftMs!, lessThan(r.firstFinalMs!), reason: 'draft did not come before the final: $r');
      // (b) the final source text replaces the draft, and none is left behind.
      expect(r.draftAtFinal, anyOf('', '…'), reason: 'draft still shown when the final arrived: $r');
      expect(r.draftAfter, '', reason: 'draft left after the round: $r');
    }
    for (final r in off) {
      expect(r.firstDraftMs, isNull, reason: 'draft text shown with 顯示原文草稿 off: $r');
    }
  });
}
