import 'package:flutter/material.dart';

import '../services/pipeline.dart';

/// Full-screen dark subtitle view.
///
/// Top: tiny debug line. Middle: finished Chinese lines (newest brightest,
/// large). Bottom: gray draft Russian (partial ASR) and, while a sentence is
/// still being translated, its Russian source in gray.
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
              Text(
                _debug(),
                style: const TextStyle(
                    fontSize: 12, color: Colors.white38, fontFamily: 'monospace'),
              ),
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
                  pipeline.draftRu.isEmpty ? '…' : pipeline.draftRu,
                  style: const TextStyle(
                      fontSize: 22, color: Color(0xFF8A8A8A), height: 1.25),
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
