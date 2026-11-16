import 'package:flutter/material.dart';

import '../logic/conversation.dart';
import '../logic/phone_audio.dart';
import '../logic/speakers.dart';
import '../services/pipeline.dart';
import 'subtitle_screen.dart';

const _radius = BorderRadius.only(
  topLeft: Radius.circular(4),
  topRight: Radius.circular(16),
  bottomLeft: Radius.circular(16),
  bottomRight: Radius.circular(16),
);

const _radiusMe = BorderRadius.only(
  topLeft: Radius.circular(16),
  topRight: Radius.circular(4),
  bottomLeft: Radius.circular(16),
  bottomRight: Radius.circular(16),
);

/// One chat bubble of the subtitle view; shared by the live screen and the history detail
/// (#1311) so both look the same. [me] = right-aligned blue (my line in 對話模式), [dashed] = the
/// pending outline, [voice] > 0 = speaker colour and label (#1155).
class SubtitleBubble extends StatelessWidget {
  const SubtitleBubble(
      {super.key, required this.children, this.dashed = false, this.me = false, this.voice = 0});
  final List<Widget> children;
  final bool dashed;
  final bool me;
  final int voice;

  @override
  Widget build(BuildContext context) {
    final accent = voice > 0 && !me ? speakerAccent(voice) : null;
    final box = Container(
      constraints: const BoxConstraints(maxWidth: 560),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: dashed
            ? Colors.transparent
            : (me
                ? UiColors.me
                : accent == null
                    ? UiColors.other
                    : Color.alphaBlend(accent.withValues(alpha: 0.22), UiColors.other)),
        borderRadius: me ? _radiusMe : _radius,
      ),
      foregroundDecoration: dashed ? const _DashedDecoration() : null,
      child: Column(
          crossAxisAlignment: me ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            if (accent != null)
              Text(speakerLabel(voice),
                  style: TextStyle(
                      fontSize: 12, color: accent, fontWeight: FontWeight.w600, height: 1.4)),
            ...children,
          ]),
    );
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Align(alignment: me ? Alignment.centerRight : Alignment.centerLeft, child: box),
    );
  }
}

/// A subtitle as a bubble: source small above the translation ([mode] picks 中文 / 原文 / 雙語),
/// or its source in grey while the translation is pending. [big] is the newest-line size.
class SubtitleItem extends StatelessWidget {
  const SubtitleItem({
    super.key,
    required this.subtitle,
    required this.isNewest,
    required this.big,
    required this.mode,
    required this.secondaryFontSize,
    this.listener = false,
    this.showVoice = false,
  });
  final Subtitle subtitle;
  final bool isNewest;
  final double big;
  final SubtitleMode mode;

  /// Size of the pending source text.
  final double secondaryFontSize;

  /// Listener view (#1146): no small source line.
  final bool listener;

  /// Show the speaker colour and label (#1155).
  final bool showVoice;

  @override
  Widget build(BuildContext context) {
    final s = subtitle;
    final pending = s.zh == null;
    final fs = isNewest ? big : big * 0.85;
    const fg = UiColors.otherFg;
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
      parts = [text(s.ru, fs, fg)];
    } else if (pending) {
      // Still translating: its source text in gray.
      parts = [text(s.ru, secondaryFontSize, UiColors.draft)];
    } else {
      final zh = s.zh!;
      final src = s.ru.trim();
      final showSrc = !listener && mode == SubtitleMode.both && src.isNotEmpty && src != zh.trim();
      parts = [
        if (showSrc)
          Padding(
            padding: const EdgeInsets.only(bottom: 2),
            child: text(s.ru, (fs * 0.5).clamp(14.0, 22.0), UiColors.sub),
          ),
        text(zh, fs, fg, underline: !s.traditionalOk),
      ];
    }
    return SubtitleBubble(
        dashed: pending && mode != SubtitleMode.source,
        me: s.speaker == Speaker.me,
        voice: showVoice ? s.voice : 0,
        children: parts);
  }
}

/// Dashed outline for the pending bubble.
class _DashedDecoration extends Decoration {
  const _DashedDecoration();
  @override
  BoxPainter createBoxPainter([VoidCallback? onChanged]) => _DashedPainter();
}

class _DashedPainter extends BoxPainter {
  @override
  void paint(Canvas canvas, Offset offset, ImageConfiguration cfg) {
    final size = cfg.size;
    if (size == null) return;
    final rr = _radius.toRRect(offset & size);
    final paint = Paint()
      ..color = UiColors.draft
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    final path = Path()..addRRect(rr);
    for (final m in path.computeMetrics()) {
      for (var d = 0.0; d < m.length; d += 9) {
        canvas.drawPath(m.extractPath(d, (d + 5).clamp(0.0, m.length)), paint);
      }
    }
  }
}
