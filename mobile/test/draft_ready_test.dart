// #2030: the draft recognizer is loaded anew each time 顯示原文草稿 is switched on, so the pipeline
// reports it as not ready from the switch until the ASR worker says it is loaded (AsrDraftState).
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/pipeline.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('com.llfbandit.record/messages'), (call) async => null);

  test('switching the draft on or off clears the recognizer ready / failed state', () {
    final p = Pipeline();
    expect(p.draftRecognizerReady, isFalse);
    for (final on in [!p.prefs.sourceDraft, p.prefs.sourceDraft]) {
      p.draftRecognizerReady = true;
      p.draftRecognizerFailed = true;
      p.setSourceDraft(on);
      expect(p.prefs.sourceDraft, on);
      expect(p.draftRecognizerReady, isFalse, reason: 'on=$on');
      expect(p.draftRecognizerFailed, isFalse, reason: 'on=$on');
    }
  });

  test('switching to the state it already has keeps the reported state', () {
    final p = Pipeline();
    p.draftRecognizerReady = true;
    p.setSourceDraft(p.prefs.sourceDraft);
    expect(p.draftRecognizerReady, isTrue);
  });
}
