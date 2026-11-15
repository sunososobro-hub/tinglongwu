import 'dart:io';

import 'package:flutter/services.dart';

import 'model_importer.dart';

/// Models shipped inside install-time Play asset packs (Play installs them with the app).
///
/// Their files are app assets, not plain files, so on first launch they are copied into the
/// models dir through the normal checked import (size + SHA-256 against the manifest). A
/// side-loaded APK has no packs, so nothing is found and the manual import stays the way in.
/// Files already in the models dir (imported, adb-pushed, or from the old on-demand packs) are
/// never replaced or deleted.
class BundledModels {
  static const _ch = MethodChannel('tlwu/native');

  /// Manifest files to copy: in the packs ([inAssets]) and with no file at all at the target.
  static List<PickedDoc> docsToCopy(ModelImporter imp, Set<String> inAssets) => [
        for (final f in imp.manifest.files)
          if (inAssets.contains(f.path) && !File('${imp.modelsDir}/${f.path}').existsSync())
            PickedDoc('asset:///${f.path}', f.basename, f.size),
      ];

  /// Copies the missing bundled model files. Null when there is nothing to copy.
  static Future<ImportReport?> copyMissing(ModelImporter imp,
      {required void Function(int done, int total, String name) onProgress}) async {
    List<String> found;
    try {
      found = await _ch.invokeListMethod<String>(
              'bundledModels', {'paths': [for (final f in imp.manifest.files) f.path]}) ??
          const [];
    } catch (_) {
      return null; // e.g. an old native side without the method
    }
    final docs = docsToCopy(imp, found.toSet());
    if (docs.isEmpty) return null;
    return imp.run(docs, onProgress: onProgress);
  }
}
