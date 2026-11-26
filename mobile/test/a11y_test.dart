import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/mic_permission.dart';
import 'package:mobile/ui/a11y.dart';
import 'package:mobile/ui/notice_snack_bar.dart';
import 'package:mobile/ui/subtitle_bubble.dart';

void main() {
  testWidgets('mic button is a labelled button that taps and announces its state', (tester) async {
    final handle = tester.ensureSemantics();
    var taps = 0;
    var stopped = false;
    late StateSetter set;
    await tester.pumpWidget(MaterialApp(
      home: StatefulBuilder(builder: (c, s) {
        set = s;
        return MicSemantics(
            stopped: stopped,
            onTap: () => taps++,
            child: const SizedBox(width: 62, height: 62, child: Icon(Icons.mic)));
      }),
    ));
    expect(find.bySemanticsLabel('停止聆聽'), findsOneWidget);
    var d = tester.getSemantics(find.bySemanticsLabel('停止聆聽'));
    expect(d.getSemanticsData().flagsCollection.isButton, isTrue);
    tester.semantics.tap(find.semantics.byLabel('停止聆聽'));
    expect(taps, 1);
    set(() => stopped = true);
    await tester.pump();
    expect(find.bySemanticsLabel('開始聆聽'), findsOneWidget);
    handle.dispose();
  });

  testWidgets('disabled mic button is reported disabled', (tester) async {
    final handle = tester.ensureSemantics();
    await tester.pumpWidget(const MaterialApp(
        home: MicSemantics(stopped: true, onTap: null, child: SizedBox(width: 62, height: 62))));
    final d = tester.getSemantics(find.bySemanticsLabel('開始聆聽')).getSemanticsData();
    expect(d.flagsCollection.isButton, isTrue);
    expect(d.hasAction(SemanticsAction.tap), isFalse);
    handle.dispose();
  });

  testWidgets('status text, download progress and subtitle bubbles are live regions',
      (tester) async {
    final handle = tester.ensureSemantics();
    await tester.pumpWidget(const MaterialApp(
        home: Column(children: [
      LiveRegion(child: Text('聆聽中')),
      SubtitleBubble(children: [Text('你好')]),
      SubtitleBubble(dashed: true, children: [Text('draft')]),
    ])));
    final live = find.byWidgetPredicate(
        (w) => w is Semantics && w.properties.liveRegion == true);
    expect(live, findsNWidgets(2));
    expect(find.bySemanticsLabel(RegExp('聆聽中')), findsOneWidget);
    expect(find.bySemanticsLabel(RegExp('你好')), findsOneWidget);
    handle.dispose();
  });

  testWidgets('NamedGroup gives the control group a spoken name', (tester) async {
    final handle = tester.ensureSemantics();
    await tester.pumpWidget(const MaterialApp(
        home: NamedGroup(label: '字幕顯示方式', child: Text('中文'))));
    expect(find.bySemanticsLabel(RegExp('字幕顯示方式')), findsOneWidget);
    handle.dispose();
  });

  testWidgets('mic permission line is a live region and its retry button is spoken',
      (tester) async {
    final handle = tester.ensureSemantics();
    var retries = 0;
    late BuildContext ctx;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: Builder(builder: (c) {
      ctx = c;
      return const SizedBox();
    }))));
    ScaffoldMessenger.of(ctx).showSnackBar(
        micPermissionSnackBar(MicPermission.deniedText, onRetry: () => retries++));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 750));
    final live = find.byWidgetPredicate((w) =>
        w is Semantics &&
        w.properties.liveRegion == true &&
        w.properties.label == MicPermission.deniedText);
    expect(live, findsOneWidget);
    expect(find.bySemanticsLabel(MicPermission.deniedText), findsOneWidget);
    final retry = find.bySemanticsLabel(micRetryLabel);
    expect(retry, findsOneWidget);
    expect(tester.getSemantics(retry).getSemanticsData().flagsCollection.isButton, isTrue);
    await tester.tap(find.text(micRetryLabel));
    expect(retries, 1);
    handle.dispose();
  });

  testWidgets('denied notice: open-settings action is a spoken button and opens settings',
      (tester) async {
    final handle = tester.ensureSemantics();
    var opened = 0;
    late BuildContext ctx;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: Builder(builder: (c) {
      ctx = c;
      return const SizedBox();
    }))));
    ScaffoldMessenger.of(ctx).showSnackBar(noticeSnackBar(MicPermission.deniedText,
        onRetry: () {}, openSettings: () async { opened++; return true; }));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 750));
    final live = find.byWidgetPredicate((w) =>
        w is Semantics &&
        w.properties.liveRegion == true &&
        w.properties.label == MicPermission.deniedText);
    expect(live, findsOneWidget);
    final settings = find.bySemanticsLabel('開啟系統設定');
    expect(settings, findsOneWidget);
    expect(tester.getSemantics(settings).getSemanticsData().flagsCollection.isButton, isTrue);
    expect(find.bySemanticsLabel(micRetryLabel), findsOneWidget);
    await tester.tap(find.text('開啟系統設定'));
    expect(opened, 1);
    handle.dispose();
  });

  testWidgets('rationale line without retry has no button', (tester) async {
    final handle = tester.ensureSemantics();
    late BuildContext ctx;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: Builder(builder: (c) {
      ctx = c;
      return const SizedBox();
    }))));
    ScaffoldMessenger.of(ctx).showSnackBar(micPermissionSnackBar(MicPermission.rationale));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 750));
    expect(find.bySemanticsLabel(MicPermission.rationale), findsOneWidget);
    expect(find.bySemanticsLabel(micRetryLabel), findsNothing);
    handle.dispose();
  });
}
