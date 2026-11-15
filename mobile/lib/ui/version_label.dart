import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';

/// Small grey "v1.0.0" (versionName only, no build number). Read at runtime from the
/// app package so a bump only touches pubspec.yaml.
class VersionLabel extends StatelessWidget {
  const VersionLabel({super.key, this.loader});

  /// Test hook; defaults to the real package info.
  final Future<PackageInfo> Function()? loader;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<PackageInfo>(
      future: (loader ?? PackageInfo.fromPlatform)(),
      builder: (context, snap) {
        final v = snap.data?.version;
        if (v == null || v.isEmpty) return const SizedBox.shrink();
        return Center(
          child: Text('v$v', style: const TextStyle(fontSize: 12, color: Colors.white38)),
        );
      },
    );
  }
}
