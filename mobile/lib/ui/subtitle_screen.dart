import 'package:flutter/material.dart';

import '../logic/phone_audio.dart';
import '../logic/target_language.dart';
import '../services/pipeline.dart';
import '../services/playback_capture.dart';

/// Full-screen dark subtitle view.
///
/// Top: tiny debug line. Middle: finished Chinese lines (newest brightest,
/// large). Bottom: gray draft source text (partial ASR) and, while a sentence is
/// still being translated, its source text in gray.
class SubtitleScreen extends StatelessWidget {
  const SubtitleScreen({super.key, required this.pipeline});
  final Pipeline pipeline;

  String _debug() {
    final p = pipeline;
    String s(num? v, {int d = 1}) => v == null ? '-' : v.toStringAsFixed(d);
    final lat = p.lastLatencyMs == null ? '-' : '${s(p.lastLatencyMs! / 1000)}s';
    final aft = p.lastAfterFinalMs == null ? '-' : '${s(p.lastAfterFinalMs! / 1000)}s';
    final ttft = p.lastFirstTokenMs == null ? '-' : '${p.lastFirstTokenMs}ms';
    return 'lat $lat (final→zh $aft) | ${s(p.lastTps)} tok/s | '
        '${p.lastTokens ?? '-'} tok | ttft $ttft | q ${p.queued} | drop ${p.dropped}';
  }

  @override
  Widget build(BuildContext context) {
    final subs = pipeline.subtitles;
    final size = MediaQuery.sizeOf(context);
    final landscape = size.width > size.height;
    final big = landscape ? 46.0 : 38.0;

    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(children: [
                Expanded(
                  child: Text(
                    _debug(),
                    style: const TextStyle(
                        fontSize: 12, color: Colors.white38, fontFamily: 'monospace'),
                  ),
                ),
                if (pipeline.phoneSupported) ...[
                  _SourceToggle(pipeline: pipeline),
                  if (pipeline.source == AudioSourceKind.phone)
                    IconButton(
                      tooltip: '字幕視窗設定',
                      visualDensity: VisualDensity.compact,
                      icon: const Icon(Icons.tune, size: 20, color: Colors.white70),
                      onPressed: () => _showOverlaySettings(context, pipeline),
                    ),
                  const SizedBox(width: 8),
                ],
                PopupMenuButton<String>(
                  tooltip: 'Subtitle language',
                  initialValue: pipeline.target,
                  onSelected: pipeline.setTarget,
                  child: Text(
                    TargetLanguage.options[pipeline.target] ?? pipeline.target,
                    style: const TextStyle(fontSize: 14, color: Colors.white70),
                  ),
                  itemBuilder: (_) => [
                    for (final e in TargetLanguage.options.entries)
                      PopupMenuItem(value: e.key, child: Text(e.value)),
                  ],
                ),
              ]),
              const SizedBox(height: 8),
              Expanded(
                child: Align(
                  alignment: Alignment.bottomLeft,
                  child: SingleChildScrollView(
                    reverse: true,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        for (var i = 0; i < subs.length; i++)
                          _line(subs[i], isNewest: i == subs.length - 1, big: big),
                      ],
                    ),
                  ),
                ),
              ),
              const Divider(color: Colors.white12, height: 20),
              ConstrainedBox(
                constraints: BoxConstraints(minHeight: landscape ? 36 : 60),
                child: Text(
                  pipeline.silenceHint
                      ? silenceHintText
                      : (pipeline.draftRu.isEmpty ? '…' : pipeline.draftRu),
                  style: TextStyle(
                      fontSize: 22,
                      color: pipeline.silenceHint ? Colors.amber : const Color(0xFF8A8A8A),
                      height: 1.25),
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _line(Subtitle s, {required bool isNewest, required double big}) {
    final pending = s.zh == null;
    final text = pending ? s.ru : s.zh!;
    final color = pending
        ? const Color(0xFF8A8A8A)
        : (isNewest ? Colors.white : const Color(0xFFB0B0B0));
    final fs = pending ? 22.0 : (isNewest ? big : big * 0.75);
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Text(
        text,
        style: TextStyle(
          fontSize: fs,
          color: color,
          height: 1.3,
          fontWeight: FontWeight.w500,
          decoration: (!pending && !s.traditionalOk)
              ? TextDecoration.underline
              : TextDecoration.none,
          decorationColor: Colors.amber,
        ),
      ),
    );
  }
}

/// Mic / Phone audio switch (#998). Phone audio first explains and asks for the overlay
/// permission, then the system capture consent.
class _SourceToggle extends StatelessWidget {
  const _SourceToggle({required this.pipeline});
  final Pipeline pipeline;

  Future<void> _toggle(BuildContext context) async {
    final messenger = ScaffoldMessenger.of(context);
    final toPhone = pipeline.source == AudioSourceKind.mic;
    if (toPhone && !await PlaybackCapture.overlayAllowed()) {
      if (!context.mounted) return;
      final go = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('浮動字幕視窗'),
          content: const Text('要在其他 App（例如 YouTube）上方顯示翻譯字幕，'
              '請在下一頁允許「顯示在其他應用程式上層」。\n\n'
              '不允許也可以：字幕只會顯示在聽龍裡面。'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('略過')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('前往設定')),
          ],
        ),
      );
      if (go == true) await PlaybackCapture.requestOverlayPermission();
    }
    final msg = await pipeline
        .setSource(toPhone ? AudioSourceKind.phone : AudioSourceKind.mic);
    if (msg != null) messenger.showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    final phone = pipeline.source == AudioSourceKind.phone;
    return TextButton.icon(
      onPressed: pipeline.switchingSource ? null : () => _toggle(context),
      style: TextButton.styleFrom(
        foregroundColor: phone ? Colors.lightBlueAccent : Colors.white70,
        visualDensity: VisualDensity.compact,
      ),
      icon: Icon(phone ? Icons.phone_android : Icons.mic, size: 18),
      label: Text(phone ? '手機音訊' : '麥克風', style: const TextStyle(fontSize: 14)),
    );
  }
}

void _showOverlaySettings(BuildContext context, Pipeline pipeline) {
  showModalBottomSheet<void>(
    context: context,
    builder: (_) => ListenableBuilder(
      listenable: pipeline,
      builder: (context, _) {
        final st = pipeline.overlayStyle;
        return Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('浮動字幕視窗', style: TextStyle(fontSize: 18)),
              const SizedBox(height: 4),
              const Text('拖曳字幕可移動，拖曳右下角 ⇲ 可調寬度，點一下回到聽龍。',
                  style: TextStyle(fontSize: 13, color: Colors.white60)),
              const SizedBox(height: 12),
              Text('字級 ${st.fontSize.round()}'),
              Slider(
                value: st.fontSize,
                min: OverlayStyle.minFontSize,
                max: OverlayStyle.maxFontSize,
                divisions: (OverlayStyle.maxFontSize - OverlayStyle.minFontSize).round(),
                onChanged: (v) =>
                    pipeline.setOverlayStyle(OverlayStyle(fontSize: v, opacity: st.opacity)),
              ),
              Text('背景不透明度 ${(st.opacity * 100).round()}%'),
              Slider(
                value: st.opacity,
                divisions: 20,
                onChanged: (v) =>
                    pipeline.setOverlayStyle(OverlayStyle(fontSize: st.fontSize, opacity: v)),
              ),
            ],
          ),
        );
      },
    ),
  );
}
