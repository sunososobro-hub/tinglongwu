import 'package:flutter/material.dart';

import '../logic/conversation.dart';
import '../logic/pack_status.dart';
import '../logic/target_language.dart';
import '../services/pipeline.dart';

/// 對話翻譯 settings (#1276): two separate pickers (我的語言 / 對方語言), what the phone can do
/// with the chosen 對方語言 (hear it, read aloud) with a download button when a pack is missing,
/// and the speaker toggle on its own. Opened by tapping the header pill.
void showConversationSheet(BuildContext context, Pipeline pipeline, {VoidCallback? onInstall}) {
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (_) => ListenableBuilder(
      listenable: pipeline,
      builder: (context, _) => ConversationSheet(pipeline: pipeline, onInstall: onInstall),
    ),
  );
}

IconData packMarkIcon(PackMark m) => switch (m) {
      PackMark.voice => Icons.volume_up,
      PackMark.download => Icons.download,
      PackMark.textOnly => Icons.subject,
    };

class ConversationSheet extends StatelessWidget {
  const ConversationSheet({super.key, required this.pipeline, this.onInstall});
  final Pipeline pipeline;

  /// Starts installing the missing packs of the chosen 對方語言; null = no button.
  final VoidCallback? onInstall;

  static const _h = TextStyle(fontSize: 15, fontWeight: FontWeight.w700);
  static const _hint = TextStyle(fontSize: 13, color: Colors.white60, height: 1.4);

  Widget _statusRow(bool ok, String text, {bool warn = false}) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(ok ? Icons.check_circle : (warn ? Icons.download_for_offline : Icons.info_outline),
              size: 18,
              color: ok ? Colors.greenAccent : (warn ? Colors.amber : Colors.white54)),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 14, height: 1.35))),
        ]),
      );

  @override
  Widget build(BuildContext context) {
    final c = pipeline.conversation;
    final st = pipeline.packStatus(c.foreign);
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.88),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('對話翻譯', style: TextStyle(fontSize: 18)),
              const SizedBox(height: 14),
              const Text('我的語言', style: _h),
              const SizedBox(height: 6),
              Wrap(spacing: 8, runSpacing: 4, children: [
                for (final code in TargetLanguage.spoken)
                  ChoiceChip(
                    key: Key('mine:$code'),
                    label: Text(TargetLanguage.options[code]!),
                    selected: c.mineLang == code,
                    onSelected: (_) => pipeline.setMineLang(code),
                  ),
              ]),
              const SizedBox(height: 16),
              const Text('對方語言', style: _h),
              const SizedBox(height: 6),
              Wrap(spacing: 8, runSpacing: 4, children: [
                for (final code in Conversation.foreignOptions.keys)
                  if (code != c.mineLang)
                    ChoiceChip(
                      key: Key('partner:$code'),
                      avatar: Icon(packMarkIcon(pipeline.packStatus(code).mark), size: 16),
                      label: Text(TargetLanguage.options[code]!),
                      selected: c.foreign == code,
                      onSelected: (_) => pipeline.setPartnerLang(code),
                    ),
              ]),
              const SizedBox(height: 6),
              const Wrap(crossAxisAlignment: WrapCrossAlignment.center, children: [
                Icon(Icons.volume_up, size: 14, color: Colors.white60),
                Text(' 可朗讀　', style: _hint),
                Icon(Icons.download, size: 14, color: Colors.white60),
                Text(' 需下載　', style: _hint),
                Icon(Icons.subject, size: 14, color: Colors.white60),
                Text(' 只顯示文字', style: _hint),
              ]),
              const SizedBox(height: 12),
              Container(
                key: const Key('packStatus'),
                width: double.infinity,
                padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.06),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(TargetLanguage.options[c.foreign]!, style: _h),
                  const SizedBox(height: 4),
                  _statusRow(st.hearingOk, st.hearingLine,
                      warn: st.hearing == Hearing.packMissing),
                  _statusRow(st.voice == Voice.installed, st.voiceLine,
                      warn: st.voice == Voice.packMissing),
                  if (st.toDownload.isNotEmpty && onInstall != null) ...[
                    const SizedBox(height: 8),
                    FilledButton.icon(
                      key: const Key('sheetDownload'),
                      icon: const Icon(Icons.download),
                      label: Text(st.downloadLabel),
                      onPressed: () {
                        Navigator.pop(context);
                        onInstall!();
                      },
                    ),
                    const SizedBox(height: 2),
                    const Text('按了才會下載；建議使用 Wi-Fi。', style: _hint),
                  ],
                ]),
              ),
              const SizedBox(height: 16),
              const Text('誰在說話', style: _h),
              const SizedBox(height: 6),
              SegmentedButton<bool>(
                key: const Key('speakerMode'),
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: true, label: Text('自動判斷')),
                  ButtonSegment(value: false, label: Text('手動切換')),
                ],
                selected: {c.auto},
                onSelectionChanged: (v) => pipeline.setAutoDirection(v.first),
              ),
              const SizedBox(height: 4),
              Text(c.auto ? '依聽到的語言自動分辨是我還是對方在說話。' : '用畫面右下角的按鈕切換「我在說 / 對方在說」。',
                  style: _hint),
            ],
          ),
        ),
      ),
    );
  }
}
