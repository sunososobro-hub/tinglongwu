import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/ui/a11y.dart';
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
    expect(d.getSemanticsData().hasFlag(SemanticsFlag.isButton), isTrue);
    tester.semantics.tap(find.bySemanticsLabel('停止聆聽'));
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
    expect(d.hasFlag(SemanticsFlag.hasEnabledState), isTrue);
    expect(d.hasFlag(SemanticsFlag.isEnabled), isFalse);
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
}
