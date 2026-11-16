import 'model_manifest.dart';
import 'target_language.dart';

/// What the phone can do with one 對方語言 in 對話翻譯 (#1276): hear it, and read my translated
/// lines aloud in it. Pure: the packs come from `assets/voice_packs.json`, the installed checks
/// from the models dir, so the texts are unit-tested.

/// Hearing the other person's speech.
enum Hearing {
  /// SenseVoice hears it (zh / en / ja / ko).
  builtIn,

  /// Needs its recognizer pack, which is installed.
  packInstalled,

  /// Needs its recognizer pack, which is not installed yet.
  packMissing,

  /// No recognizer for it: my lines are translated into it, their speech is not understood.
  cannot,
}

/// Reading my translated lines aloud.
enum Voice {
  installed,

  /// A voice pack exists but is not installed.
  packMissing,

  /// No voice for this language yet: text only, nothing to download.
  none,
}

/// One-glance mark for a language chip.
enum PackMark { voice, download, textOnly }

class LangPackStatus {
  const LangPackStatus({
    required this.lang,
    required this.hearing,
    required this.voice,
    this.asrPack,
    this.voicePack,
  });

  final String lang;
  final Hearing hearing;
  final Voice voice;

  /// The recognizer / voice pack for [lang] in the manifest, if any.
  final VoicePack? asrPack;
  final VoicePack? voicePack;

  /// [packs]: every optional pack of the manifest. [hasVoice] / [hasAsr]: installed now.
  factory LangPackStatus.of(
    String lang, {
    required List<VoicePack> packs,
    required bool Function(String lang) hasVoice,
    required bool Function(String lang) hasAsr,
  }) {
    VoicePack? find(bool asr) {
      for (final p in packs) {
        if (p.lang == lang && p.isAsr == asr) return p;
      }
      return null;
    }

    final asr = find(true);
    final voice = find(false);
    return LangPackStatus(
      lang: lang,
      asrPack: asr,
      voicePack: voice,
      hearing: asr != null
          ? (hasAsr(lang) ? Hearing.packInstalled : Hearing.packMissing)
          : (TargetLanguage.spoken.contains(lang) ? Hearing.builtIn : Hearing.cannot),
      // A voice that is on the phone counts even when the manifest does not list it.
      voice: hasVoice(lang) ? Voice.installed : (voice != null ? Voice.packMissing : Voice.none),
    );
  }

  /// Packs the user can download for this language, recognizer first (never automatic).
  List<VoicePack> get toDownload => [
        if (hearing == Hearing.packMissing) asrPack!,
        if (voice == Voice.packMissing) voicePack!,
      ];

  int get downloadBytes => toDownload.fold(0, (a, p) => a + p.size);

  PackMark get mark => toDownload.isNotEmpty
      ? PackMark.download
      : (voice == Voice.installed ? PackMark.voice : PackMark.textOnly);

  bool get hearingOk => hearing == Hearing.builtIn || hearing == Hearing.packInstalled;

  String get _name => TargetLanguage.options[lang] ?? lang;

  /// Button text, '' when there is nothing to download: 「下載語音包 (112 MB)」.
  String get downloadLabel {
    final d = toDownload;
    if (d.isEmpty) return '';
    final what = d.length > 1 ? '辨識包和語音包' : (d.first.isAsr ? '辨識包' : '語音包');
    return '下載$what (${packSize(downloadBytes)})';
  }

  String get hearingLine => switch (hearing) {
        Hearing.builtIn => '聽懂對方：可以（內建）',
        Hearing.packInstalled => '聽懂對方：可以（辨識包已安裝）',
        Hearing.packMissing => '聽懂對方：需要下載$_name辨識包（${packSize(asrPack!.size)}）',
        Hearing.cannot => '聽懂對方：還不行。只能把我說的話翻成$_name給對方看',
      };

  String get voiceLine => switch (voice) {
        Voice.installed => '朗讀給對方聽：可以（語音包已安裝）',
        Voice.packMissing => '朗讀給對方聽：需要下載語音包（${packSize(voicePack!.size)}）',
        Voice.none => '朗讀給對方聽：這個語言還沒有語音，只顯示文字',
      };
}

/// "112 MB" / "1.30 GB" (binary units, as on the Import screen).
String packSize(int bytes) => bytes >= (1 << 30)
    ? '${(bytes / (1 << 30)).toStringAsFixed(2)} GB'
    : '${(bytes / (1 << 20)).toStringAsFixed(0)} MB';
