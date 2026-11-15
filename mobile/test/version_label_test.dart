import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:mobile/ui/version_label.dart';

void main() {
  testWidgets('shows versionName only, without build number', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: VersionLabel(
          loader: () async => PackageInfo(
              appName: 'x', packageName: 'x', version: '0.2.0', buildNumber: '85'),
        ),
      ),
    ));
    await tester.pump();
    expect(find.text('v0.2.0'), findsOneWidget);
    expect(find.textContaining('85'), findsNothing);
  });
}
