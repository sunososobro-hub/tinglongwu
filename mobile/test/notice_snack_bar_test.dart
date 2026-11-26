import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/mic_permission.dart';
import 'package:mobile/ui/notice_snack_bar.dart';

void main() {
  Future<void> show(WidgetTester t, SnackBar bar) async {
    await t.pumpWidget(MaterialApp(
      home: Scaffold(body: Builder(builder: (c) => TextButton(
          onPressed: () => ScaffoldMessenger.of(c).showSnackBar(bar), child: const Text('go')))),
    ));
    await t.tap(find.text('go'));
    await t.pump();
    await t.pump(const Duration(milliseconds: 750)); // SnackBar entrance animation
  }

  testWidgets('denied notice has a button that opens the system settings', (t) async {
    var opened = 0;
    await show(t, noticeSnackBar(MicPermission.deniedText, openSettings: () async { opened++; return true; }));
    expect(find.text('開啟系統設定'), findsOneWidget);
    await t.tap(find.text('開啟系統設定'));
    expect(opened, 1);
  });

  testWidgets('other notices have no settings button', (t) async {
    await show(t, noticeSnackBar(MicPermission.rationale, openSettings: () async => fail('no')));
    expect(find.text('開啟系統設定'), findsNothing);
  });
}
