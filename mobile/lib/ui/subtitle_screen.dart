import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:flutter/material.dart';

import '../logic/conversation.dart';
import '../logic/debug_stats.dart';
import '../logic/phone_audio.dart';
import '../logic/speech_out.dart';
import '../logic/target_language.dart';
import '../services/pipeline.dart';
import '../services/playback_capture.dart';
import 'advanced_screen.dart';

/// Palette shared with the desktop app (tinglongwu/gui/app.py), dark values.
class UiColors {
  static const bg = Color(0xFF14182E);
  static const headerTop = Color(0xFF1D2447);
  static const headerBottom = Color(0xFF2C2350);
  static const other = Color(0xFF2B3250);
  static const otherFg = Color(0xFFF2F4FF);
  static const me = Color(0xFF3F5FE0);
  static const accent = Color(0xFF4A63D8);
  static const sub = Color(0xFFA9B1D6);
  static const draft = Color(0xFF8A8A8A);
  static const line = Color(0xFF2A3052);
  static const dotIdle = Color(0xFF8A8A8A);
  static const dotLoading = Color(0xFFE0A000);
  static const dotOn = Color(0xFF2EA043);
  static const dotError = Color(0xFFD0453A);
}

/// Footer state: idle (stopped) / loading (switching source) / listening / error.
enum ListenState { idle, loading, on, error }

extension ListenStateUi on ListenState {
  String get label => const {
        ListenState.idle: '待機',
        ListenState.loading: '載入中',
        ListenState.on: '聆聽中',
        ListenState.error: '錯誤',
      }[this]!;
  Color get dot => const {
        ListenState.idle: UiColors.dotIdle,
        ListenState.loading: UiColors.dotLoading,
        ListenState.on: UiColors.dotOn,
        ListenState.error: UiColors.dotError,
      }[this]!;
}

ListenState listenStateOf(Pipeline p) {
  if (p.stage == Stage.error || p.stage == Stage.missingModels) return ListenState.error;
  if (p.switchingSource || p.stage != Stage.listening) return ListenState.loading;
  return p.paused ? ListenState.idle : ListenState.on;
}

/// 對話模式 is on and applies (microphone only, #558).
bool conversationActive(Pipeline p) => p.conversation.enabled && p.source == AudioSourceKind.mic;

/// Empty-state hint (differs for 看影片翻譯).
String emptyHint({required bool phone}) =>
    phone ? '播放影片，翻譯字幕會出現在這裡' : '對著手機說話，翻譯字幕會出現在這裡';

/// Dark conversation view: header (mode, settings, language pill), one bubble per finished
/// line (small source above, large translation below), a dashed grey bubble for the pending
/// draft, and a footer with status + a round start/stop mic button. Listen-only lines are left
/// "other" bubbles; in 對話模式 (#558) my lines are right "me" bubbles.
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

  /// Grey draft text (#1064; not in 中文-only mode); '…' = speech going on, no text yet.
  String _draftText() {
    final d = pipeline.draftRu;
    if (d.isEmpty || (d != '…' && pipeline.overlayStyle.mode == SubtitleMode.zh)) return '…';
    return d;
  }

  @override
  Widget build(BuildContext context) {
    final subs = pipeline.subtitles;
    final size = MediaQuery.sizeOf(context);
    final landscape = size.width > size.height;
    final big = pipeline.overlayStyle.screenFontSize(landscape: landscape);
    final mode = pipeline.overlayStyle.mode;
    final phone = pipeline.source == AudioSourceKind.phone;
    final draft = pipeline.silenceHint ? silenceHintText : (pipeline.paused ? '' : _draftText());
    final showDraft = draft.isNotEmpty && (pipeline.silenceHint || draft != '…' || subs.isNotEmpty);
    final empty = subs.isEmpty && !showDraft;

    return Scaffold(
      backgroundColor: UiColors.bg,
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Header(
              pipeline: pipeline,
              debug: showDebugStats(releaseMode: kReleaseMode) ? _debug() : null),
          Expanded(
            child: empty
                ? _EmptyState(phone: phone)
                : Align(
                    alignment: Alignment.bottomCenter,
                    child: SingleChildScrollView(
                      reverse: true,
                      padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          for (var i = 0; i < subs.length; i++)
                            _line(subs[i],
                                isNewest: i == subs.length - 1, big: big, mode: mode),
                          if (showDraft)
                            _bubble(
                              dashed: true,
                              children: [
                                Text(
                                  draft,
                                  style: TextStyle(
                                      fontSize: pipeline.overlayStyle.secondaryFontSize,
                                      color: pipeline.silenceHint
                                          ? Colors.amber
                                          : UiColors.draft,
                                      height: 1.25),
                                ),
                              ],
                            ),
                        ],
                      ),
                    ),
                  ),
          ),
          _Footer(pipeline: pipeline),
        ],
      ),
    );
  }

  static const _radius = BorderRadius.only(
    topLeft: Radius.circular(4),
    topRight: Radius.circular(16),
    bottomLeft: Radius.circular(16),
    bottomRight: Radius.circular(16),
  );

  static const _radiusMe = BorderRadius.only(
    topLeft: Radius.circular(16),
    topRight: Radius.circular(4),
    bottomLeft: Radius.circular(16),
    bottomRight: Radius.circular(16),
  );

  Widget _bubble({required List<Widget> children, bool dashed = false, bool me = false}) {
    final box = Container(
      constraints: const BoxConstraints(maxWidth: 560),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: dashed ? Colors.transparent : (me ? UiColors.me : UiColors.other),
        borderRadius: me ? _radiusMe : _radius,
      ),
      foregroundDecoration: dashed ? const _DashedDecoration() : null,
      child: Column(
          crossAxisAlignment: me ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: children),
    );
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Align(alignment: me ? Alignment.centerRight : Alignment.centerLeft, child: box),
    );
  }

  Widget _line(Subtitle s,
      {required bool isNewest, required double big, required SubtitleMode mode}) {
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
      parts = [text(s.ru, pipeline.overlayStyle.secondaryFontSize, UiColors.draft)];
    } else {
      final zh = s.zh!;
      final src = s.ru.trim();
      final showSrc = mode == SubtitleMode.both && src.isNotEmpty && src != zh.trim();
      parts = [
        if (showSrc)
          Padding(
            padding: const EdgeInsets.only(bottom: 2),
            child: text(s.ru, (fs * 0.5).clamp(14.0, 22.0), UiColors.sub),
          ),
        text(zh, fs, fg, underline: !s.traditionalOk),
      ];
    }
    return _bubble(
        dashed: pending && mode != SubtitleMode.source,
        me: s.speaker == Speaker.me,
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
    final rr = SubtitleScreen._radius.toRRect(offset & size);
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

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.phone});
  final bool phone;
  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(phone ? Icons.ondemand_video : Icons.mic_none,
                size: 40, color: UiColors.sub.withValues(alpha: 0.6)),
            const SizedBox(height: 10),
            Text(emptyHint(phone: phone),
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 14, color: UiColors.sub, height: 1.6)),
          ]),
        ),
      );
}

class _Header extends StatelessWidget {
  const _Header({required this.pipeline, this.debug});
  final Pipeline pipeline;
  final String? debug;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [UiColors.headerTop, UiColors.headerBottom],
        ),
      ),
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 6, 8, 12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            if (debug != null)
              Text(debug!,
                  style: const TextStyle(
                      fontSize: 11, color: Colors.white38, fontFamily: 'monospace')),
            Row(children: [
              Expanded(
                child: pipeline.phoneSupported
                    ? Align(
                        alignment: Alignment.centerLeft,
                        child: _SourceToggle(pipeline: pipeline))
                    : const Text(CaptureModeText.micLabel,
                        style: TextStyle(
                            fontSize: 15, fontWeight: FontWeight.w700, color: Colors.white)),
              ),
              if (pipeline.source == AudioSourceKind.mic)
                IconButton(
                  key: const Key('conversationToggle'),
                  tooltip: pipeline.conversation.enabled ? '關閉對話模式' : '對話模式（面對面雙向翻譯）',
                  visualDensity: VisualDensity.compact,
                  icon: Icon(pipeline.conversation.enabled ? Icons.forum : Icons.forum_outlined,
                      size: 20,
                      color: pipeline.conversation.enabled ? Colors.lightBlueAccent : Colors.white70),
                  onPressed: () => pipeline.setConversationEnabled(!pipeline.conversation.enabled),
                ),
              IconButton(
                tooltip: '字幕顯示設定',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.settings, size: 20, color: Colors.white70),
                onPressed: () => _showOverlaySettings(context, pipeline),
              ),
            ]),
            const SizedBox(height: 6),
            if (conversationActive(pipeline))
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                    '對話模式  ${pipeline.conversation.pairLabel}  · '
                    '${voiceStatusLabel(installed: pipeline.ttsInstalled, speaking: pipeline.speaking)}',
                    style: const TextStyle(
                        fontSize: 12, color: Colors.white, fontWeight: FontWeight.w700)),
              )
            else
              PopupMenuButton<String>(
              tooltip: '字幕要翻成哪種語言',
              initialValue: pipeline.target,
              onSelected: pipeline.setTarget,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  const Text(TargetLanguage.sourceShort,
                      style: TextStyle(fontSize: 12, color: UiColors.sub)),
                  const Text('  →  ', style: TextStyle(fontSize: 12, color: UiColors.sub)),
                  Text(TargetLanguage.options[TargetLanguage.normalize(pipeline.target)]!,
                      style: const TextStyle(
                          fontSize: 12, color: Colors.white, fontWeight: FontWeight.w700)),
                  const Icon(Icons.arrow_drop_down, size: 16, color: UiColors.sub),
                ]),
              ),
              itemBuilder: (_) => [
                for (final code in TargetLanguage.options.keys)
                  PopupMenuItem(value: code, child: Text(TargetLanguage.targetLabel(code))),
              ],
            ),
          ]),
        ),
      ),
    );
  }
}

class _Footer extends StatelessWidget {
  const _Footer({required this.pipeline});
  final Pipeline pipeline;

  @override
  Widget build(BuildContext context) {
    final st = listenStateOf(pipeline);
    final stopped = pipeline.paused;
    return Container(
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: UiColors.line)),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
          child: Row(children: [
            Expanded(
              child: Row(children: [
                Container(
                  key: const Key('statusDot'),
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(color: st.dot, shape: BoxShape.circle),
                ),
                const SizedBox(width: 6),
                Text(st.label, style: const TextStyle(fontSize: 12, color: UiColors.sub)),
              ]),
            ),
            Tooltip(
              message: stopped ? '開始聆聽' : '停止聆聽',
              child: GestureDetector(
                key: const Key('micButton'),
                onTap: pipeline.switchingSource ? null : () => pipeline.setPaused(!stopped),
                child: Container(
                  width: 62,
                  height: 62,
                  decoration: BoxDecoration(
                    color: UiColors.accent,
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                          color: UiColors.accent.withValues(alpha: stopped ? 0 : 0.22),
                          spreadRadius: 8),
                    ],
                  ),
                  child: Icon(stopped ? Icons.mic_off : Icons.mic,
                      size: 28, color: Colors.white),
                ),
              ),
            ),
            Expanded(
              child: conversationActive(pipeline)
                  ? Align(alignment: Alignment.centerRight, child: _SideButton(pipeline: pipeline))
                  : const SizedBox(),
            ),
          ]),
        ),
      ),
    );
  }
}

/// 對話模式: one tap switches who is talking (#558).
class _SideButton extends StatelessWidget {
  const _SideButton({required this.pipeline});
  final Pipeline pipeline;

  @override
  Widget build(BuildContext context) {
    final me = pipeline.conversation.side == Speaker.me;
    return TextButton.icon(
      key: const Key('sideButton'),
      style: TextButton.styleFrom(
        foregroundColor: Colors.white,
        backgroundColor: me ? UiColors.me : UiColors.other,
        shape: const StadiumBorder(),
        padding: const EdgeInsets.symmetric(horizontal: 10),
      ),
      onPressed: pipeline.switchSpeaker,
      icon: const Icon(Icons.swap_horiz, size: 16),
      label: Text(pipeline.conversation.sideLabel, style: const TextStyle(fontSize: 12)),
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
    if (toPhone && CapturePlatform.current().explainsBeforeStart) {
      // iOS (#1076): the system broadcast sheet comes next; say what to tap and what to expect.
      final go = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text(CaptureModeText.phoneLabel),
          content: const SingleChildScrollView(child: Text(CaptureModeText.iosStartExplain)),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('取消')),
            FilledButton(
                onPressed: () => Navigator.pop(c, true),
                child: const Text(CaptureModeText.iosStartButton)),
          ],
        ),
      );
      if (go != true) return;
    } else if (toPhone && !await PlaybackCapture.overlayAllowed()) {
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
    final color = phone ? Colors.lightBlueAccent : Colors.white;
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
        final plat = CapturePlatform.current();
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
              const SizedBox(height: 8),
              Text('字幕大小 ${st.fontSize.round()}（聽龍畫面和浮動視窗都適用）'),
              Slider(
                value: st.fontSize,
                min: OverlayStyle.minFontSize,
                max: OverlayStyle.maxFontSize,
                divisions: (OverlayStyle.maxFontSize - OverlayStyle.minFontSize).round(),
                onChanged: (v) => pipeline.setOverlayStyle(st.copyWith(fontSize: v)),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('顯示原文草稿'),
                subtitle: const Text('一句話還沒說完時，先用灰字顯示聽到的原文，說完後換成正式字幕。'
                    '會多用一些耗電和記憶體。',
                    style: TextStyle(fontSize: 13, color: Colors.white60)),
                value: pipeline.prefs.sourceDraft,
                onChanged: pipeline.setSourceDraft,
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('進階功能'),
                subtitle: const Text('中文草稿、手機速度測試與建議',
                    style: TextStyle(fontSize: 13, color: Colors.white60)),
                trailing: const Icon(Icons.chevron_right),
                onTap: () {
                  Navigator.pop(context);
                  Navigator.of(context).push(
                      MaterialPageRoute<void>(builder: (_) => AdvancedScreen(pipeline: pipeline)));
                },
              ),
              if (pipeline.phoneSupported) ...[
                const SizedBox(height: 8),
                Text(plat.overlayTitle, style: const TextStyle(fontSize: 16)),
                const SizedBox(height: 4),
                Text(plat.overlayHelp,
                    style: const TextStyle(fontSize: 13, color: Colors.white60)),
                const SizedBox(height: 12),
                if (plat.overlayOpacityAdjustable) ...[
                  Text('背景不透明度 ${(st.opacity * 100).round()}%'),
                  Slider(
                    value: st.opacity,
                    divisions: 20,
                    onChanged: (v) => pipeline.setOverlayStyle(st.copyWith(opacity: v)),
                  ),
                ],
              ],
            ],
          ),
        );
      },
    ),
  );
}
