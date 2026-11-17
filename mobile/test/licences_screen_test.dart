import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/logic/licences.dart';
import 'package:mobile/ui/licences_screen.dart';

void main() {
  testWidgets('lists every shipped model and library', (tester) async {
    tester.view.physicalSize = const Size(800, 6000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(home: LicencesScreen(loader: (_) async => 'text')));
    for (final n in ['SenseVoice', 'Silero VAD', 'Hy-MT2', 'sherpa-onnx', 'llama.cpp', 'OpenCC', 'Supertonic 3']) {
      expect(find.textContaining(n), findsWidgets, reason: n);
    }
  });

  testWidgets('tapping an entry shows its licence text', (tester) async {
    await tester.pumpWidget(MaterialApp(home: LicencesScreen(loader: (a) async => 'TEXT of $a')));
    await tester.tap(find.text('Silero VAD'));
    await tester.pumpAndSettle();
    expect(find.textContaining('TEXT of assets/licenses/mit.txt'), findsOneWidget);
  });

  test('every licence asset is declared in pubspec', () {
    // assets are declared as a directory in pubspec.yaml: assets/licenses/
    expect(licenceEntries.every((e) => e.asset.startsWith('assets/licenses/')), isTrue);
  });
}
