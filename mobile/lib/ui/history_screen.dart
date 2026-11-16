import 'package:flutter/material.dart';

import '../logic/conversation.dart';
import '../logic/history.dart';
import '../services/history_store.dart';
import '../services/pipeline.dart';
import 'subtitle_bubble.dart';
import 'subtitle_screen.dart' show UiColors;

/// "2026-11-16 14:32" (local time).
String historyDateLabel(DateTime t) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '${t.year}-${two(t.month)}-${two(t.day)} ${two(t.hour)}:${two(t.minute)}';
}

/// "12 分鐘" / "不到 1 分鐘".
String historyDurationLabel(HistorySession s) {
  final m = ((s.endMs - s.startMs) / 60000).round();
  return m < 1 ? '不到 1 分鐘' : '$m 分鐘';
}

/// Asks before deleting; true = go ahead.
Future<bool> confirmHistoryDelete(BuildContext context,
    {required String title, required String body, required String action}) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (c) => AlertDialog(
      title: Text(title),
      content: Text(body),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('取消')),
        FilledButton(
            key: const Key('confirmDelete'),
            onPressed: () => Navigator.pop(c, true),
            child: Text(action)),
      ],
    ),
  );
  return ok == true;
}

/// 字幕紀錄 (#1311): the sessions saved on this phone, newest first. Swipe or long-press a row to
/// delete it; 「刪除全部紀錄」 in the top bar. Everything stays on the device: there is no share or
/// export here.
class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key, required this.pipeline});
  final Pipeline pipeline;

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  List<HistorySession>? _sessions;

  HistoryRecorder? get _rec => widget.pipeline.history;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final r = _rec;
    final list = r == null ? <HistorySession>[] : await r.store.list();
    if (mounted) setState(() => _sessions = list);
  }

  /// Asks, then deletes the file; true = deleted.
  Future<bool> _delete(HistorySession s) async {
    final ok = await confirmHistoryDelete(context,
        title: '刪除這筆紀錄？',
        body: '${historyDateLabel(s.start)} ${s.mode.label}\n刪除後無法復原。',
        action: '刪除');
    if (!ok) return false;
    await _rec?.delete(s.id);
    return true;
  }

  Future<void> _deleteAll() async {
    final ok = await confirmHistoryDelete(context,
        title: '刪除全部紀錄？', body: '這支手機上所有字幕紀錄都會被刪除，無法復原。', action: '全部刪除');
    if (!ok) return;
    await _rec?.deleteAll();
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final sessions = _sessions;
    return Scaffold(
      backgroundColor: UiColors.bg,
      appBar: AppBar(
        title: const Text('字幕紀錄'),
        actions: [
          TextButton.icon(
            key: const Key('deleteAll'),
            onPressed: sessions == null || sessions.isEmpty ? null : _deleteAll,
            icon: const Icon(Icons.delete_outline, size: 18),
            label: const Text('刪除全部紀錄'),
          ),
        ],
      ),
      body: ListenableBuilder(
        listenable: widget.pipeline,
        builder: (context, _) {
          final on = widget.pipeline.prefs.saveHistory;
          return Column(children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 10, 16, 6),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                    on
                        ? '字幕文字只存在這支手機上，30 天後自動刪除。不存錄音。'
                        : '「保存字幕紀錄」已關閉，新的翻譯不會被保存（可在字幕設定打開）。',
                    key: const Key('historyNote'),
                    style: TextStyle(
                        fontSize: 13, color: on ? UiColors.sub : Colors.amber, height: 1.4)),
              ),
            ),
            Expanded(
              child: sessions == null
                  ? const Center(child: CircularProgressIndicator())
                  : sessions.isEmpty
                      ? const Center(
                          child: Text('還沒有紀錄',
                              key: Key('historyEmpty'),
                              style: TextStyle(fontSize: 15, color: UiColors.sub)))
                      : ListView.separated(
                          itemCount: sessions.length,
                          separatorBuilder: (_, _) =>
                              const Divider(height: 1, color: UiColors.line),
                          itemBuilder: (context, i) => _row(context, sessions[i]),
                        ),
            ),
          ]);
        },
      ),
    );
  }

  Widget _row(BuildContext context, HistorySession s) {
    return Dismissible(
      key: ValueKey(s.id),
      direction: DismissDirection.endToStart,
      confirmDismiss: (_) => _delete(s),
      onDismissed: (_) {
        _sessions?.remove(s);
        if (mounted) setState(() {});
      },
      background: Container(
        color: Colors.redAccent,
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 20),
        child: const Icon(Icons.delete, color: Colors.white),
      ),
      child: ListTile(
        key: Key('session_${s.id}'),
        title: Text('${historyDateLabel(s.start)} · ${s.mode.label}',
            style: const TextStyle(fontSize: 15)),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 2),
          child: Text(
              '${s.languageLabel} · ${s.lines.length} 句 · ${historyDurationLabel(s)}\n${s.preview}',
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 13, color: UiColors.sub, height: 1.4)),
        ),
        isThreeLine: true,
        onLongPress: () async {
          if (await _delete(s)) await _load();
        },
        onTap: () async {
          await Navigator.of(context).push(MaterialPageRoute<void>(
              builder: (_) => HistoryDetailScreen(pipeline: widget.pipeline, session: s)));
          if (mounted) _load(); // it may have been deleted there
        },
      ),
    );
  }
}

/// One saved session as bubbles, drawn like the live subtitle view (same bubbles; 中文 / 原文 /
/// 雙語 follows the live setting; conversation lines of mine on the right).
class HistoryDetailScreen extends StatelessWidget {
  const HistoryDetailScreen({super.key, required this.pipeline, required this.session});
  final Pipeline pipeline;
  final HistorySession session;

  /// A saved line as the live view's [Subtitle]. Conversation side 0 = me.
  static Subtitle subtitleOf(HistoryLine l) => Subtitle(l.src,
      zh: l.tr, speaker: l.side == 0 ? Speaker.me : Speaker.them);

  @override
  Widget build(BuildContext context) {
    final style = pipeline.overlayStyle;
    final landscape = MediaQuery.sizeOf(context).width > MediaQuery.sizeOf(context).height;
    final big = style.screenFontSize(landscape: landscape) * 0.8;
    final lines = session.lines;
    return Scaffold(
      backgroundColor: UiColors.bg,
      appBar: AppBar(
        title: Text('${historyDateLabel(session.start)} · ${session.mode.label}',
            style: const TextStyle(fontSize: 16)),
        actions: [
          IconButton(
            key: const Key('deleteOne'),
            tooltip: '刪除這筆紀錄',
            icon: const Icon(Icons.delete_outline),
            onPressed: () async {
              final nav = Navigator.of(context);
              final ok = await confirmHistoryDelete(context,
                  title: '刪除這筆紀錄？', body: '刪除後無法復原。', action: '刪除');
              if (!ok) return;
              await pipeline.history?.delete(session.id);
              nav.pop();
            },
          ),
        ],
      ),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
          child: Align(
            alignment: Alignment.centerLeft,
            child: Text(
                '${session.languageLabel} · ${lines.length} 句 · ${historyDurationLabel(session)}',
                style: const TextStyle(fontSize: 13, color: UiColors.sub)),
          ),
        ),
        Expanded(
          child: lines.isEmpty
              ? const Center(
                  child: Text('這筆紀錄沒有字幕', style: TextStyle(fontSize: 15, color: UiColors.sub)))
              : ListView(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
                  children: [
                    for (final l in lines)
                      SubtitleItem(
                        subtitle: subtitleOf(l),
                        isNewest: true,
                        big: big,
                        mode: style.mode,
                        secondaryFontSize: style.secondaryFontSize,
                      ),
                  ],
                ),
        ),
      ]),
    );
  }
}
