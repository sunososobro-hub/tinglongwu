import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../logic/licences.dart';

/// Open-source licences (#1347): every model and native library the app ships, with its licence text (offline).
class LicencesScreen extends StatelessWidget {
  const LicencesScreen({super.key, this.loader});

  /// Test hook; defaults to the bundled asset.
  final Future<String> Function(String asset)? loader;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Open-source licences')),
      body: ListView(children: [
        for (final e in licenceEntries)
          ListTile(
            title: Text(e.name),
            subtitle: Text('${e.licence}\n${e.credit}'),
            isThreeLine: true,
            onTap: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => _LicenceText(entry: e, loader: loader ?? rootBundle.loadString))),
          ),
      ]),
    );
  }
}

class _LicenceText extends StatelessWidget {
  const _LicenceText({required this.entry, required this.loader});
  final LicenceEntry entry;
  final Future<String> Function(String asset) loader;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(entry.name)),
      body: FutureBuilder<String>(
        future: loader(entry.asset),
        builder: (_, s) => SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Text(s.data ?? (s.hasError ? 'Could not load licence text.' : '…'),
              style: const TextStyle(fontSize: 12)),
        ),
      ),
    );
  }
}
