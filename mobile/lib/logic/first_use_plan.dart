import 'model_manifest.dart';

/// What the person ticked on the first-use choice screen (#1931). The default (nothing ticked) is
/// "just watch with subtitles": only the base download.
class FirstUseChoice {
  const FirstUseChoice({this.conversation = false, this.voice = false, this.otherLang});

  /// 對話翻譯: the other person's speech is recognised too.
  final bool conversation;

  /// Read the translation aloud.
  final bool voice;

  /// The other person's language code, or null for none.
  final String? otherLang;
}

/// One thing the first-use download fetches: the base zip ([pack] null) or one optional pack.
class FirstUseItem {
  const FirstUseItem({required this.id, required this.bytes, this.pack});
  final String id;
  final int bytes;
  final VoicePack? pack;
  bool get isBase => pack == null;
}

class FirstUsePlan {
  const FirstUsePlan(this.items);

  /// The base zip first, then the chosen packs.
  final List<FirstUseItem> items;

  int get totalBytes => items.fold(0, (a, i) => a + i.bytes);
  List<VoicePack> get packs => [for (final i in items) ?i.pack];
}

/// The ordered download list for [choice] (pure). [packs] is the manifest's optional packs;
/// [baseBytes] the base zip size. A language with no pack adds nothing; a pack is never listed
/// twice.
FirstUsePlan firstUsePlan(FirstUseChoice choice, List<VoicePack> packs, {required int baseBytes}) {
  final lang = choice.otherLang;
  final picked = <VoicePack>[];
  void add(VoicePack? p) {
    if (p != null && !picked.any((q) => q.id == p.id)) picked.add(p);
  }

  if (lang != null && lang.isNotEmpty) {
    if (choice.voice) add(packs.where((p) => !p.isAsr && p.lang == lang).firstOrNull);
    if (choice.conversation) add(packs.where((p) => p.isAsr && p.lang == lang).firstOrNull);
  }
  return FirstUsePlan([
    FirstUseItem(id: 'base', bytes: baseBytes),
    for (final p in picked) FirstUseItem(id: p.id, bytes: p.size, pack: p),
  ]);
}
