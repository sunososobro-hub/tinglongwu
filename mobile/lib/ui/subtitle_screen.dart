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
                if (pipeline.phoneSupported) _SourceToggle(pipeline: pipeline),
                IconButton(
                  tooltip: '字幕顯示設定',
                  visualDensity: VisualDensity.compact,
                  icon: const Icon(Icons.tune, size: 20, color: Colors.white70),
                  onPressed: () => _showOverlaySettings(context, pipeline),
                ),
                const SizedBox(width: 8),
                PopupMenuButton<String>(
                  tooltip: '字幕要翻成哪種語言',
                  initialValue: pipeline.target,
                  onSelected: pipeline.setTarget,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        TargetLanguage.targetLabel(pipeline.target),
                        style: const TextStyle(fontSize: 14, color: Colors.white70),
                      ),
                      const Text(
                        TargetLanguage.sourceHint,
                        style: TextStyle(fontSize: 10, color: Colors.white54),
                      ),
                    ],
                  ),
                  itemBuilder: (_) => [
                    for (final code in TargetLanguage.options.keys)
                      PopupMenuItem(value: code, child: Text(TargetLanguage.targetLabel(code))),
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
                          _line(subs[i],
                              isNewest: i == subs.length - 1,
                              big: big,
                              mode: pipeline.overlayStyle.mode),
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

  Widget _line(Subtitle s,
      {required bool isNewest, required double big, required SubtitleMode mode}) {
    final pending = s.zh == null;
    final fs = isNewest ? big : big * 0.75;
    final bright = isNewest ? Colors.white : const Color(0xFFB0B0B0);
    const gray = Color(0xFF8A8A8A);
    Widget text(String t, double size, Color color, {bool underline = false}) => Text(
          t,
          style: TextStyle(
            fontSize: size,
            color: color,
            height: 1.3,
            fontWeight: FontWeight.w500,
            decoration: underline ? TextDecoration.underline : TextDecoration.none,
            decorationColor: Colors.amber,
          ),
        );
    final List<Widget> parts;
    if (mode == SubtitleMode.source) {
      parts = [text(s.ru, fs, bright)];
    } else if (pending) {
      // Still translating: its source text in gray.
      parts = [text(s.ru, 22, gray)];
    } else {
      final zh = s.zh!;
      final src = s.ru.trim();
      final showSrc = mode == SubtitleMode.both && src.isNotEmpty && src != zh.trim();
      parts = [
        if (showSrc) text(s.ru, (fs * 0.5).clamp(16.0, 24.0), gray),
        text(zh, fs, bright, underline: !s.traditionalOk),
      ];
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: parts),
    );
  }
}

/// 說話翻譯 (mic) / 看影片翻譯 (phone playback) menu (#998); each option has a one-line hint.
/// 看影片翻譯 first explains and asks for the overlay permission, then the system consent.
class _SourceToggle extends StatelessWidget {
  const _SourceToggle({required this.pipeline});
  final Pipeline pipeline;

  Future<void> _select(BuildContext context, bool toPhone) async {
    if (toPhone == (pipeline.source == AudioSourceKind.phone)) return;
    final messenger = ScaffoldMessenger.of(context);
    if (toPhone && !await PlaybackCapture.overlayAllowed()) {
      if (!context.mounted) return;
      final go = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text(CaptureModeText.phoneLabel),
          content: const Text('看影片翻譯會把字幕浮在影片上（例如 YouTube）。'
              '請在下一頁允許「顯示在其他應用程式上層」。\n\n'
              '不允許也可以：字幕只會顯示在聽龍裡面。\n\n'
              '字幕只顯示在你的手機上，不會錄音或存檔。請只用於個人觀看，並尊重影片的著作權。'),
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
    final color = phone ? Colors.lightBlueAccent : Colors.white70;
    Widget option(bool p) => ListTile(
          contentPadding: EdgeInsets.zero,
          leading: Icon(p ? Icons.ondemand_video : Icons.record_voice_over),
          title: Text(CaptureModeText.label(phone: p)),
          subtitle: Text(CaptureModeText.hint(phone: p)),
          trailing: p == phone ? const Icon(Icons.check, size: 18) : null,
        );
    return PopupMenuButton<bool>(
      tooltip: '切換翻譯方式',
      enabled: !pipeline.switchingSource,
      onSelected: (p) => _select(context, p),
      itemBuilder: (_) => [
        PopupMenuItem(value: false, child: option(false)),
        PopupMenuItem(value: true, child: option(true)),
      ],
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 170),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(phone ? Icons.ondemand_video : Icons.record_voice_over, size: 18, color: color),
          const SizedBox(width: 6),
          Flexible(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(CaptureModeText.label(phone: phone),
                    style: TextStyle(fontSize: 14, color: color)),
                Text(CaptureModeText.hint(phone: phone),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 10, color: Colors.white54)),
              ],
            ),
          ),
          Icon(Icons.arrow_drop_down, size: 18, color: color),
        ]),
      ),
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
              const Text('字幕顯示', style: TextStyle(fontSize: 18)),
              const SizedBox(height: 8),
              SegmentedButton<SubtitleMode>(
                showSelectedIcon: false,
                segments: [
                  for (final m in SubtitleMode.values)
                    ButtonSegment(value: m, label: Text(m.label)),
                ],
                selected: {st.mode},
                onSelectionChanged: (v) => pipeline.setSubtitleMode(v.first),
              ),
              const SizedBox(height: 4),
              const Text('雙語：原文在上（小字），譯文在下。聽龍畫面和浮動視窗都適用。',
                  style: TextStyle(fontSize: 13, color: Colors.white60)),
              if (pipeline.phoneSupported) ...[
                const SizedBox(height: 16),
                const Text('浮動字幕視窗', style: TextStyle(fontSize: 16)),
                const SizedBox(height: 4),
                const Text('拖曳字幕可移動，拖曳右下角 ⇲ 可調寬度，高度隨內容增加。點一下回到聽龍。',
                    style: TextStyle(fontSize: 13, color: Colors.white60)),
                const SizedBox(height: 12),
                Text('字級 ${st.fontSize.round()}'),
                Slider(
                  value: st.fontSize,
                  min: OverlayStyle.minFontSize,
                  max: OverlayStyle.maxFontSize,
                  divisions: (OverlayStyle.maxFontSize - OverlayStyle.minFontSize).round(),
                  onChanged: (v) => pipeline.setOverlayStyle(st.copyWith(fontSize: v)),
                ),
                Text('背景不透明度 ${(st.opacity * 100).round()}%'),
                Slider(
                  value: st.opacity,
                  divisions: 20,
                  onChanged: (v) => pipeline.setOverlayStyle(st.copyWith(opacity: v)),
                ),
              ],
            ],
          ),
        );
      },
    ),
  );
}
